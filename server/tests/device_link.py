"""设备档案与连接替身：登记信任、保持在线的 WS 会话、下发帧捕获。"""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from tests.frames import hello_frame, recv_until


def register_trusted_device(device_id: str, capabilities: list[str] | None = None) -> None:
    """在设备档案里登记并信任一台设备（默认无能力声明，即未声明态）。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(
        device_id, "0.4.13", 57, "4.4.4", capabilities or [], "127.0.0.1", "2026-06-01T08:00:00.000Z"
    )
    devices_repository.set_trusted(device_id, True)


@contextmanager
def trusted_online(
    client: TestClient,
    device_id: str,
    admin_headers: dict[str, str],
    *,
    version_code: int = 6,
    capabilities: list[str] | None = None,
) -> Iterator[WebSocketTestSession]:
    """建档、信任、重连，产出一个保持在线的设备 WS 会话。

    上下文管理器保证断言失败时连接也会关闭：泄漏的连接会让 TestClient 的 portal
    挂起，表现为整个测试会话卡死。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(hello_frame(device_id, version_code=version_code, capabilities=capabilities))
        recv_until(ws, "link.hello_ack")
    client.patch(f"/api/v1/devices/{device_id}", headers=admin_headers, json={"trusted": True})
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(hello_frame(device_id, version_code=version_code, capabilities=capabilities))
        recv_until(ws, "link.hello_ack")
        yield ws


class FakeGatewayWS:
    """替代设备控制连接：捕获管理面下发的帧，供断言读取。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        # 由 send_text 置位；测试用 wait_for_sent 等到帧真的发出，不靠固定时长。
        self._sent_event = asyncio.Event()

    async def send_text(self, data: str) -> bool:
        self.sent.append(data)
        self._sent_event.set()
        return True

    async def wait_for_sent(self, count: int = 1) -> None:
        """等到至少发出 [count] 帧。事件循环内推进，不读墙上时钟。"""
        while len(self.sent) < count:
            self._sent_event.clear()
            await self._sent_event.wait()

    def last_frame(self) -> dict[str, Any]:
        return dict(json.loads(self.sent[-1]))


class DeadGatewayWS:
    """发送即失败的设备连接：验证下发路径对发送失败的处置。"""

    async def send_text(self, data: str) -> bool:
        message = "connection gone"
        raise RuntimeError(message)
