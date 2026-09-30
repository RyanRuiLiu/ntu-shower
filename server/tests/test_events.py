"""事件总线测试：订阅/广播/退订/满队列丢弃。"""

import asyncio

from app.core import events


def test_publish_reaches_subscriber() -> None:
    async def flow() -> None:
        sid, queue = events.subscribe()
        events.publish("door_update", {"device_id": "d1"})
        msg = await asyncio.wait_for(queue.get(), timeout=1)
        events.unsubscribe(sid)
        assert '"door_update"' in msg
        assert '"d1"' in msg

    asyncio.run(flow())


def test_unsubscribe_stops_delivery() -> None:
    async def flow() -> None:
        sid, queue = events.subscribe()
        events.unsubscribe(sid)
        events.publish("door_update", {"device_id": "d1"})
        assert queue.empty()

    asyncio.run(flow())


def test_full_queue_drops_silently() -> None:
    async def flow() -> None:
        sid, queue = events.subscribe()
        for _ in range(300):
            events.publish("door_update", {"n": 1})
        assert queue.qsize() <= 256
        events.unsubscribe(sid)

    asyncio.run(flow())
