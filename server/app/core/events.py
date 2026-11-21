"""进程内事件总线：领域变化推送给 /stream 的订阅者。

跨线程语义：publish 可能来自事件循环线程或工作线程，而 asyncio.Queue 只能在其
所属循环上操作，投递一律经 loop.call_soon_threadsafe 回到订阅者所在循环。

事件类型常量在此登记，网页端 core/contract.js 由 tools/sync_contracts.py 从本
模块生成；命名用点号，与设备协议一致。
"""

import asyncio
import contextlib
import itertools
import json
import threading
from dataclasses import dataclass

from .protocol import JsonObject

QUEUE_MAX_SIZE = 256

#: 一台设备的在场或名称变了（上线、下线、改名）。payload 是精简档案
#: （device_id、name、online、at）：版本、IP、时钟偏差等运维诊断字段不进公开
#: 推送，管理台要详情走 /devices/runtime。发布只经 devices.service 的
#: publish_device_update 一处。
DEVICE_UPDATE = "device.update"

#: 喷头现态变了。payload 是该设备的**完整现态行**（device_id、at、doors：与
#: GET /doors 同一 service 路径产出的 DoorView 数组）——WS 单连接有序投递，
#: 数据承载推送是行业标准（Supabase/Firebase 同型）：客户端直接应用、不回拉，
#: 请求量与事件量解耦。无变化的上报也照发，充当状态再同步。
DOOR_UPDATE = "door.update"


@dataclass(frozen=True)
class _Subscriber:
    queue: "asyncio.Queue[str]"
    loop: asyncio.AbstractEventLoop


_subscribers: dict[int, _Subscriber] = {}
_seq = itertools.count(1)
_lock = threading.Lock()


def subscribe() -> "tuple[int, asyncio.Queue[str]]":
    """注册订阅者，返回 (订阅号, 队列)；队列有界，满则丢弃新事件。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAX_SIZE)
    sid = next(_seq)
    with _lock:
        _subscribers[sid] = _Subscriber(queue, loop)
    return sid, queue


def unsubscribe(sid: int) -> None:
    with _lock:
        _subscribers.pop(sid, None)


def publish(event_type: str, payload: JsonObject) -> None:
    """非阻塞广播；队列满则丢弃并继续（推送是尽力而为，现态可经 REST 获取）。"""
    message = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":"), ensure_ascii=False)
    with _lock:
        subscribers = list(_subscribers.items())
    for sid, sub in subscribers:
        try:
            sub.loop.call_soon_threadsafe(_offer, sub.queue, message)
        except RuntimeError:
            # 订阅者的事件循环已关闭（连接早已消失）：清理该订阅，不影响其余订阅者
            unsubscribe(sid)


def _offer(queue: asyncio.Queue[str], message: str) -> None:
    """在订阅者所属事件循环上投递一条事件；队列满则丢弃（推送是尽力而为）。"""
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(message)
