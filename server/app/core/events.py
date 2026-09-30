"""进程内事件总线：领域变化 → /api/v1/stream 订阅者推送。

单进程内使用，模块间解耦的最小实现；将来拆分进程时此接口可平滑替换为
Redis pub/sub。

跨线程语义：publish 可能来自事件循环线程（网关）或工作线程（
run_in_threadpool 里的领域服务），而 asyncio.Queue 只允许在其所属事件循环上
操作。因此投递一律经 loop.call_soon_threadsafe 回到订阅者所在循环，而不是
在工作线程里直接 put_nowait。

## 事件类型登记

事件的**唯一来源**在这里：服务端 `publish` 用下面这些常量，网页端从
`core/contract.js` 取由本模块生成的那一份（`tools/sync_contracts.py`）。此前两边
各自硬编码字符串——改名时改了一边忘另一边，表现是界面上某些状态再也不更新，而且
哪里都不报错。

命名用点号，与设备协议（`link.hello`、`door.snapshot`）一致。此前这里是下划线，
同一套系统两种分隔符。
"""

import asyncio
import contextlib
import itertools
import json
import threading
from dataclasses import dataclass

from .protocol import JsonObject

QUEUE_MAX_SIZE = 256

#: 一台设备的档案变了：上线、下线、改名、调整信任位都推它。
#:
#: payload 是**完整**的 `DeviceRecord`。不做成 `device_online` / `device_offline` 两个
#: 事件：那是同一个字段的两个值，前端要写两个做同样事情的分支，而设备重连时变的不只是
#: `online`——版本号、来源 IP、开机时刻都在服务端这一侧刚更新过，只推状态词的话它们
#: 到不了界面。
DEVICE_UPDATE = "device.update"

#: 喷头状态变了。
#:
#: payload 只列**哪几个**喷头变了（`door_no` / `state` / `card_no`），不是完整现态：
#: 完整的里面还有分配时间、学号等十几个字段，逐条推会把 payload 撑大，而前端收到后本
#: 来就要重拉一次全量。
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
    """非阻塞广播；队列满则丢弃并继续（推送是尽力而为，现态可经 REST 兜取）。"""
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
