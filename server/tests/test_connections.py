"""连接注册表的发送超时：对端停止读取时按离线处理，不无限阻塞调用方。"""

import asyncio
from typing import Any, cast

import pytest

from app.core import connections


class _StuckWebSocket:
    async def send_text(self, data: str) -> None:
        # 永不完成：模拟对端停止读取，只有发送超时能结束这次调用
        await asyncio.Event().wait()


def test_send_to_returns_false_when_peer_stops_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connections, "SEND_TIMEOUT_S", 0.01)
    manager = connections.ConnectionManager()
    manager.register("dev-stuck", cast(Any, _StuckWebSocket()))

    assert asyncio.run(manager.send_to("dev-stuck", "frame")) is False
