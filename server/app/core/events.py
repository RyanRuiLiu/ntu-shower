"""进程内事件总线：doors 状态变化 → /api/v1/stream 订阅者推送。

单进程单事件循环内使用 asyncio.Queue，模块间解耦的最小实现；
将来拆分进程时此接口可平滑替换为 Redis pub/sub。
"""

import asyncio
import json

_subscribers: dict[int, asyncio.Queue] = {}
_seq = iter(range(1, 1 << 62))


def subscribe() -> tuple[int, asyncio.Queue]:
    sid = next(_seq)
    _subscribers[sid] = asyncio.Queue(maxsize=256)
    return sid, _subscribers[sid]


def unsubscribe(sid: int) -> None:
    _subscribers.pop(sid, None)


def publish(event_type: str, payload: dict) -> None:
    """非阻塞广播；队列满时丢弃并继续（推送是尽力而为，现态可经 REST 兜取）。"""
    message = json.dumps({"type": event_type, "payload": payload}, separators=(",", ":"), ensure_ascii=False)
    for queue in list(_subscribers.values()):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass
