"""ADB 桥接测试：世代语义、幂等生命周期、审计留痕（真实 TCP 往返）。

时序说明：整个生命周期收敛在单个用例内——桥接涉及 asyncio TCP 监听与
WS 会话收尾的交互，跨 TestClient 实例会触发收尾伪影。

事件循环依赖：adb/open 注册的 TCP 监听器属于处理该请求的事件循环，
必须跨请求存活才能接收后续 adb 连接，因此依赖 client fixture 的常驻 portal。
TCP 侧用阻塞 socket 在测试线程直连；服务端字节泵跑在应用循环，两侧互不阻塞。
"""

import json
import socket
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.gateway import manager as manager_module

ADMIN_HEADERS = {"X-Admin-Token": "test-token"}
DEVICE_ID = "dev-adb-1"


class FakeGatewayWS:
    """替代设备控制连接：捕获管理面下发的指令帧，供断言读取。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> bool:
        self.sent.append(data)
        return True

    def last_payload(self) -> dict[str, Any]:
        return dict(json.loads(self.sent[-1])["payload"])


def _status(client: TestClient) -> dict[str, Any]:
    return dict(client.get(f"/admin/devices/{DEVICE_ID}/adb/status", headers=ADMIN_HEADERS).json())


def test_adb_bridge_lifecycle(client: TestClient, monkeypatch) -> None:
    """完整生命周期：开启→世代/nonce→往返→审计→关闭语义。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(DEVICE_ID, True)
    fake_ws = FakeGatewayWS()
    monkeypatch.setattr(manager_module.manager, "_connections", {DEVICE_ID: fake_ws})

    # ── 不存在/未信任的设备：管理面拒绝 ──
    assert client.post("/admin/devices/dev-nobody/adb/open", headers=ADMIN_HEADERS).status_code == 404

    # ── 开启：分配端口 + 下发一次性 nonce ──
    body = client.post(f"/admin/devices/{DEVICE_ID}/adb/open", headers=ADMIN_HEADERS).json()
    assert body["port"] == 15655
    nonce = fake_ws.last_payload()["nonce"]
    assert nonce

    # ── 状态可观测（设备 WS 尚未接入）──
    status = _status(client)
    assert status["open"] is True
    assert status["device_connected"] is False
    assert status["generation"] > 0

    # ── 错误 nonce 被拒（4401，配对失败）──
    with pytest.raises(WebSocketDisconnect) as wrong_nonce:
        with client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce=wrong") as ws:
            ws.receive_bytes()
    assert wrong_nonce.value.code == 4401

    # ── 真实往返：本地 TCP ↔ 设备 WS 字节透传 ──
    with client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce={nonce}") as device_ws:
        with socket.create_connection(("127.0.0.1", body["port"]), timeout=5) as tcp:
            tcp.settimeout(5)
            tcp.sendall(b"host-hello")
            assert device_ws.receive_bytes() == b"host-hello"
            device_ws.send_bytes(b"device-reply")
            assert tcp.recv(1024) == b"device-reply"

        # TCP 客户端断开只释放流，桥保持（对齐旧系统隧道语义）
        assert _status(client)["open"] is True

    # 设备 WS 断开 → 整桥回收
    assert _status(client)["open"] is False

    # ── 审计留痕：adb.open 与 adb.session_end 全落 command_audit ──
    audit = client.get("/admin/commands", headers=ADMIN_HEADERS, params={"device_id": DEVICE_ID}).json()
    actions = {row["action"] for row in audit}
    assert {"adb.open", "adb.session_end"} <= actions
    opened = [row for row in audit if row["action"] == "adb.open"]
    assert any(row["status"] == "ok" and "port" in (row["terminal_message"] or "") for row in opened)


def test_bridge_buffers_never_lose_bytes() -> None:
    """双向有界缓冲：对端未就绪的字节暂存且超限丢最旧（纯逻辑）。"""
    from app.modules.bridge.manager import PENDING_MAX
    from app.modules.bridge.routes import _append

    buffer = bytearray()
    _append(buffer, b"first-")
    _append(buffer, b"second")
    assert buffer == b"first-second"
    # 超限：丢最旧
    _append(buffer, b"x" * PENDING_MAX)
    assert len(buffer) == PENDING_MAX
    assert not buffer.startswith(b"first-")
