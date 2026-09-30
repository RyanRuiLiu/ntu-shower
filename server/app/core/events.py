"""进程内事件总线：doors 状态变化 → /api/v1/stream 订阅者推送。

单进程内使用，模块间解耦的最小实现；将来拆分进程时此接口可平滑替换为
Redis pub/sub。

跨线程语义：publish 可能来自事件循环线程（网关）或工作线程（
run_in_threadpool 里的领域服务），而 asyncio.Queue 只允许在其所属事件循环上
操作。因此投递一律经 loop.call_soon_threadsafe 回到订阅者所在循环，而不是
在工作线程里直接 put_nowait。
"""

import asyncio
import contextlib
import itertools
import json
import threading
from dataclasses import dataclass

from .protocol import JsonObject

QUEUE_MAX_SIZE = 256


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
