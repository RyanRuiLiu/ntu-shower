"""ADB 桥：open/close/status、nonce 一次性消费、设备 WS 拒绝、审计留痕与缓冲边界。"""

import asyncio
import socket
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests import device_link

from app.core.config import settings
from app.core.connections import manager
from app.modules.bridge.manager import PENDING_MAX, AdbBridge, registry
from app.modules.bridge.routes import _append

DEVICE_ID = "dev-adb-1"


def _register_online(monkeypatch: pytest.MonkeyPatch, device_id: str = DEVICE_ID) -> device_link.FakeGatewayWS:
    device_link.register_trusted_device(device_id)
    fake = device_link.FakeGatewayWS()
    monkeypatch.setattr(manager, "_connections", {device_id: fake})
    return fake


def test_bridge_requires_trusted_online_admin(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开桥仅管理面可触发，且设备必须存在、已信任、在线。"""
    assert client.post("/api/v1/devices/dev-nobody/bridge/open", headers=admin_headers).status_code == 404

    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.4.13", 57, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    assert client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers).status_code == 403

    assert client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open").status_code == 401


def test_bridge_lifecycle_with_nonce_and_tcp_roundtrip(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完整生命周期：开桥、nonce 配对、双向字节透传、回收、审计留痕。"""
    fake = _register_online(monkeypatch)

    body = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers).json()
    assert body["port"] == settings.bridge_base_port
    assert body["already_open"] is False
    nonce = fake.last_frame()["payload"]["nonce"]
    assert nonce

    status: dict[str, Any] = client.get(f"/api/v1/devices/{DEVICE_ID}/bridge", headers=admin_headers).json()
    assert status["open"] is True
    assert status["device_connected"] is False
    assert status["generation"] > 0
    assert status["ssh_target"]

    # 错误 nonce 被拒（4401），且不拆掉已建好的桥
    with (
        pytest.raises(WebSocketDisconnect) as rejected,
        client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce=wrong") as ws,
    ):
        ws.receive_bytes()
    assert rejected.value.code == 4401
    assert registry.get(DEVICE_ID) is not None

    # 真实往返：本地 TCP ↔ 设备 WS 字节透传
    with client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce={nonce}") as device_ws:
        with socket.create_connection(("127.0.0.1", body["port"]), timeout=5) as tcp:
            tcp.settimeout(5)
            tcp.sendall(b"host-hello")
            assert device_ws.receive_bytes() == b"host-hello"
            device_ws.send_bytes(b"device-reply")
            assert tcp.recv(1024) == b"device-reply"

        # TCP 客户端断开只释放流，桥保持
        assert client.get(f"/api/v1/devices/{DEVICE_ID}/bridge", headers=admin_headers).json()["open"] is True

    # 设备 WS 断开 → 整桥回收（含监听套接字），再开桥不撞端口
    reopened = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers)
    assert reopened.status_code == 200
    assert reopened.json()["already_open"] is False
    client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/close", headers=admin_headers)

    audit = client.get("/api/v1/audit/commands", headers=admin_headers, params={"device_id": DEVICE_ID}).json()
    recorded = {row["action"] for row in audit}
    assert {"bridge.open", "bridge.session_end"} <= recorded
    opened = [row for row in audit if row["action"] == "bridge.open"]
    assert any(row["status"] == "ok" and "port" in (row["terminal_message"] or "") for row in opened)


def test_bridge_buffers_until_both_halves_arrive(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任意一半先到，字节都要缓冲到另一半接上后送达：TCP 先到与设备先到各验一遍。"""
    # 设备先到：设备发往本机的字节先入缓冲，adb 客户端连上后收到
    fake_first = _register_online(monkeypatch, "dev-bridge-devfirst")
    opened = client.post("/api/v1/devices/dev-bridge-devfirst/bridge/open", headers=admin_headers).json()
    nonce_first = fake_first.last_frame()["payload"]["nonce"]
    with client.websocket_connect(f"/gateway/bridge?device_id=dev-bridge-devfirst&nonce={nonce_first}") as device_ws:
        device_ws.send_bytes(b"early-device-bytes")
        # 不等待固定时长：TCP 端有 5 秒读超时，字节无论此刻已入缓冲还是稍后到达都能收到
        with socket.create_connection(("127.0.0.1", opened["port"]), timeout=5) as tcp:
            tcp.settimeout(5)
            assert tcp.recv(1024) == b"early-device-bytes"
    client.post("/api/v1/devices/dev-bridge-devfirst/bridge/close", headers=admin_headers)

    # TCP 先到：本机发往设备的字节先入缓冲，设备连入后冲给设备
    fake_second = _register_online(monkeypatch, "dev-bridge-tcpfirst")
    opened = client.post("/api/v1/devices/dev-bridge-tcpfirst/bridge/open", headers=admin_headers).json()
    with socket.create_connection(("127.0.0.1", opened["port"]), timeout=5) as tcp:
        tcp.settimeout(5)
        tcp.sendall(b"early-host-bytes")
        nonce_second = fake_second.last_frame()["payload"]["nonce"]
        with client.websocket_connect(
            f"/gateway/bridge?device_id=dev-bridge-tcpfirst&nonce={nonce_second}"
        ) as device_ws:
            assert device_ws.receive_bytes() == b"early-host-bytes"
    client.post("/api/v1/devices/dev-bridge-tcpfirst/bridge/close", headers=admin_headers)


def test_bridge_open_is_idempotent_while_registered(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册表里已有桥时重复 open：返回同一端口并标记 already_open。"""
    _register_online(monkeypatch)

    first = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers).json()
    second = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers).json()

    assert second["already_open"] is True
    assert second["port"] == first["port"]
    assert second["generation"] == first["generation"]


def test_bridge_open_reports_offline_when_send_fails(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连接存在但发送失败：409，且不残留 pending/active 状态。"""
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: device_link.DeadGatewayWS()})

    r = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers)

    assert r.status_code == 409
    assert DEVICE_ID not in registry.pending
    assert registry.get(DEVICE_ID) is None


def test_bridge_open_reports_port_unavailable(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端口被占：退避重试后仍失败 → 409，且不残留桥状态。"""
    # 重试次数与节奏是模块常量：等真实退避要 3 秒，测试把节奏压到毫秒级，
    # 只验证"重试穷尽后失败"这一行为本身
    monkeypatch.setattr("app.modules.bridge.manager.BIND_ATTEMPTS", 3)
    monkeypatch.setattr("app.modules.bridge.manager.BIND_BACKOFF_S", 0.01)
    _register_online(monkeypatch)
    holder = socket.socket()
    holder.bind(("127.0.0.1", settings.bridge_base_port))
    holder.listen(1)
    try:
        r = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers)
    finally:
        holder.close()

    assert r.status_code == 409
    assert registry.get(DEVICE_ID) is None


def test_bridge_close_when_not_open(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/close", headers=admin_headers)

    assert r.status_code == 200
    assert r.json() == {"device_id": DEVICE_ID, "closed": False}


def test_bridge_close_recycles_active_bridge(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """活跃桥可被管理面关闭并回收；重复关闭返回 False。"""
    _register_online(monkeypatch)
    client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers)

    assert client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/close", headers=admin_headers).json()["closed"] is True
    assert client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/close", headers=admin_headers).json()["closed"] is False
    assert registry.get(DEVICE_ID) is None


def test_bridge_status_when_closed(client: TestClient, admin_headers: dict[str, str]) -> None:
    """桥关着时也给出 ssh_target（网页端要靠它拼连接命令）。"""
    status: dict[str, Any] = client.get(f"/api/v1/devices/{DEVICE_ID}/bridge", headers=admin_headers).json()

    assert status["open"] is False
    assert status["ssh_target"]
    assert "port" not in status


def test_bridge_surface_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    """桥的状态查询同样是 admin 面。"""
    assert client.get(f"/api/v1/devices/{DEVICE_ID}/bridge").status_code == 401
    assert client.get(f"/api/v1/devices/{DEVICE_ID}/bridge", headers=ops_headers).status_code == 403


def test_nonce_is_consumed_on_first_use(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nonce 一次性：配对即失效，重放通不过。"""
    fake = _register_online(monkeypatch)
    client.post(f"/api/v1/devices/{DEVICE_ID}/bridge/open", headers=admin_headers)
    nonce = fake.last_frame()["payload"]["nonce"]

    with client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce={nonce}"):
        pass  # 配对成功即消费；连接随即断开，整桥回收

    with (
        pytest.raises(WebSocketDisconnect) as replayed,
        client.websocket_connect(f"/gateway/bridge?device_id={DEVICE_ID}&nonce={nonce}") as ws,
    ):
        ws.receive_bytes()
    assert replayed.value.code == 4401


def test_bridge_buffer_appends_with_bound() -> None:
    """有界缓冲：对端未就绪时暂存字节，超出上限丢最旧。"""
    buffer = bytearray()
    _append(buffer, b"first-")
    _append(buffer, b"second")
    assert buffer == b"first-second"

    _append(buffer, b"x" * PENDING_MAX)
    assert len(buffer) == PENDING_MAX
    assert not buffer.startswith(b"first-")


def test_adb_bridge_recycle_is_idempotent() -> None:
    bridge = AdbBridge("dev-unit", 15655, 1)
    assert bridge.connected is False
    assert bridge.has_adb_client is False

    asyncio.run(bridge.recycle("test"))
    asyncio.run(bridge.recycle("test"))
