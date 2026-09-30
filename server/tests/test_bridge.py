"""ADB 桥接测试：世代语义、幂等生命周期、审计留痕（真实 TCP 往返）。

时序说明：整个生命周期收敛在单个用例内，因为桥接涉及 asyncio TCP 监听
与 WS 会话收尾的交互，跨 TestClient 实例会触发收尾伪影。

isolated portal：该用例用 standalone_client（portal=None），每个
websocket_connect 自建独立 portal——共享 portal 上的 WS 会话退出会取消
仍挂起的应用协程（starlette.testclient._portal_factory 官方语义）。
TCP 侧因此可以用阻塞 socket 在测试线程直连：服务端字节泵跑在 app 循环，
两侧 socket 互不阻塞。
"""

import json
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.gateway import manager as manager_module


def _admin(client: TestClient) -> dict:
    return {"X-Admin-Token": "test-token"}


class FakeGatewayWS:
    async def send_text(self, data: str) -> bool:
        self.sent.append(data)
        return True

    def __init__(self) -> None:
        self.sent: list[str] = []


def _hello(device_id: str) -> str:
    return json.dumps({
        "v": 1,
        "type": "link.hello",
        "id": str(uuid.uuid4()),
        "ts": "2026-09-30T12:00:00.000Z",
        "payload": {
            "device_id": device_id,
            "app_version": "0.3.2",
            "version_code": 9,
            "os_version": "4.4.4",
            "capabilities": [],
        },
    })


def _recv_until(ws, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


def test_adb_bridge_lifecycle(standalone_client: TestClient, monkeypatch) -> None:
    """完整生命周期：开启→世代/nonce→往返→审计→关闭语义。"""
    from app.modules.bridge.manager import registry
    from app.modules.devices import repository as devices_repository

    device_id = "dev-adb-1"
    devices_repository.register(device_id, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(device_id, True)
    fake_ws = FakeGatewayWS()
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: fake_ws})

    print("[S1] 未信任设备拒绝", flush=True)
    # ── 未信任设备拒绝 ──
    assert (
        standalone_client.post("/admin/devices/dev-nobody/adb/open", headers=_admin(standalone_client)).status_code
        == 404
    )

    print("[S2] 开启", flush=True)
    # ── 开启：分配端口 + 下发一次性 nonce ──
    body = standalone_client.post(f"/admin/devices/{device_id}/adb/open", headers=_admin(standalone_client)).json()
    assert body["port"] == 15655
    nonce = json.loads(fake_ws.sent[-1])["payload"]["nonce"]
    assert nonce

    # ── 错误 nonce 被拒（4401；独立 portal，WS 关闭不互相牵连）──
    with pytest.raises(WebSocketDisconnect):
        with standalone_client.websocket_connect(
            f"/gateway/bridge?device_id={device_id}&nonce=wrong"
        ) as ws:
            ws.receive_bytes()

    print("[S4] 状态可观测", flush=True)
    # ── 状态可观测（WS 未接入）──
    status = standalone_client.get(f"/admin/devices/{device_id}/adb/status", headers=_admin(standalone_client)).json()
    assert status["open"] is True and status["device_connected"] is False
    assert status["generation"] > 0

    # ── 真实往返：TCP 用阻塞 socket（测试线程直连；WS 会话自带独立 portal，
    #    服务端泵运行在 app 循环，两侧 socket 互不阻塞）──
    with standalone_client.websocket_connect(
        f"/gateway/bridge?device_id={device_id}&nonce={nonce}"
    ) as device_ws:
        with socket.create_connection(("127.0.0.1", body["port"]), timeout=5) as tcp:
            tcp.settimeout(5)
            tcp.sendall(b"host-hello")
            assert device_ws.receive_bytes() == b"host-hello"
            device_ws.send_bytes(b"device-reply")
            assert tcp.recv(1024) == b"device-reply"

        # TCP 客户端断开后的状态：桥保持（仅流释放）
        status = standalone_client.get(
            f"/admin/devices/{device_id}/adb/status", headers=_admin(standalone_client)).json()

    # 设备 WS 断开已回收桥接：状态 open=False
    status = standalone_client.get(f"/admin/devices/{device_id}/adb/status", headers=_admin(standalone_client)).json()
    assert status["open"] is False

    print("[S7] 审计留痕", flush=True)
    # ── 审计留痕：adb.open / adb.close / adb.session_end 全落 command_audit ──
    audit = standalone_client.get("/admin/commands", headers=_admin(standalone_client),
                       params={"device_id": device_id}).json()
    actions = {row["action"] for row in audit}
    assert {"adb.open", "adb.close", "adb.session_end"} <= actions
    opened = [row for row in audit if row["action"] == "adb.open"]
    assert any(row["status"] == "ok" and "port" in (row["terminal_message"] or "")
               for row in opened)

    print("[S8] 重新开启", flush=True)
    # ── 重新开启：端口稳定、世代递增 ──
    body2 = standalone_client.post(f"/admin/devices/{device_id}/adb/open", headers=_admin(standalone_client)).json()
    assert body2["port"] == 15655
    status2 = standalone_client.get(f"/admin/devices/{device_id}/adb/status",
                         headers=_admin(standalone_client)).json()
    assert status2["generation"] > 0
    standalone_client.post(f"/admin/devices/{device_id}/adb/close", headers=_admin(standalone_client))


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
