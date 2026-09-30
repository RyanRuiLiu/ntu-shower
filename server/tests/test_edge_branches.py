"""边界分支补测：安全自检、协议降级、事件总线清理、桥接失败路径。"""

import asyncio
import io
import socket

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import protocol, security
from app.gateway import manager as manager_module

ADMIN_HEADERS = {"X-Admin-Token": "test-token"}


class FakeGatewayWS:
    """替代设备控制连接：捕获下发的指令帧。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> bool:
        self.sent.append(data)
        return True


class DeadGatewayWS:
    """发送即失败的设备连接：验证网关的失败降级。"""

    async def send_text(self, data: str) -> bool:
        message = "connection gone"
        raise RuntimeError(message)


def _register_trusted(device_id: str) -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register(device_id, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(device_id, True)


# ── 安全自检 ──────────────────────────────────────────────


def test_placeholder_admin_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """占位令牌必须让启动自检失败（OWASP ASVS V2.1 禁止默认凭证）。"""
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "admin_token", "change-me")
    with pytest.raises(security.AdminTokenNotConfiguredError):
        security.assert_admin_token_configured()


def test_real_admin_token_passes_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "admin_token", "a-real-ops-token")
    security.assert_admin_token_configured()  # 不抛即通过


def test_admin_guard_rejects_missing_and_wrong_token(client: TestClient) -> None:
    assert client.get("/admin/devices").status_code == 401
    assert client.get("/admin/devices", headers={"X-Admin-Token": "wrong"}).status_code == 401


# ── 协议辅助：外部输入一律降级不抛 ─────────────────────────


def test_protocol_helpers_tolerate_malformed_frames() -> None:
    assert protocol.payload_of({}) == {}
    assert protocol.payload_of({"payload": "not-a-dict"}) == {}
    assert protocol.payload_of({"payload": {"a": 1}}) == {"a": 1}
    assert protocol.text_of({"id": 123}, "id") == ""
    assert protocol.text_of({"id": "abc"}, "id") == "abc"
    assert protocol.list_of({"capabilities": "nope"}, "capabilities") == []
    assert protocol.list_of({"capabilities": [1, 2]}, "capabilities") == [1, 2]
    assert protocol.as_int("42") == 42
    assert protocol.as_int(None, default=7) == 7
    assert protocol.as_int("abc") == 0
    assert protocol.parse("not json") is None
    assert protocol.parse("[1,2]") is None


# ── 事件总线 ──────────────────────────────────────────────


def test_events_publish_cleans_up_dead_subscriber() -> None:
    """订阅者的事件循环已关闭：投递被丢弃并清理订阅，不影响其他订阅者。"""
    from app.core import events

    loop = asyncio.new_event_loop()
    sid, _queue = loop.run_until_complete(_subscribe())
    loop.close()

    events.publish("door_update", {"probe": True})  # 不得抛出

    assert sid not in events._subscribers


async def _subscribe() -> tuple[int, asyncio.Queue[str]]:
    from app.core import events

    return events.subscribe()


def test_events_unsubscribe_is_idempotent() -> None:
    from app.core import events

    async def flow() -> None:
        sid, _queue = events.subscribe()
        events.unsubscribe(sid)
        events.unsubscribe(sid)  # 重复调用不抛

    asyncio.run(flow())


# ── 桥接失败路径 ──────────────────────────────────────────


def test_bridge_open_reports_offline_when_send_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备连接存在但发送失败：409，且不残留 pending/active 状态。"""
    from app.modules.bridge.manager import registry

    device_id = "dev-send-fail"
    _register_trusted(device_id)
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: DeadGatewayWS()})

    r = client.post(f"/admin/devices/{device_id}/adb/open", headers=ADMIN_HEADERS)

    assert r.status_code == 409
    assert device_id not in registry.pending
    assert device_id not in registry.active


def test_bridge_open_reports_port_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """端口被占：三次退避重试后仍失败 → 409，且不残留桥状态。"""
    from app.modules.bridge.manager import BASE_PORT, registry

    device_id = "dev-port-busy"
    _register_trusted(device_id)
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: FakeGatewayWS()})

    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    holder.bind(("127.0.0.1", BASE_PORT))
    holder.listen(1)
    try:
        r = client.post(f"/admin/devices/{device_id}/adb/open", headers=ADMIN_HEADERS)
    finally:
        holder.close()

    assert r.status_code == 409
    assert device_id not in registry.active


def test_bridge_rejects_stale_generation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """桥内已有更新世代时，旧世代 nonce 接入被拒（4409）。

    判定必须是严格大于——相等表示本次 open 刚建立的桥正等待设备接管。
    """
    from app.modules.bridge import routes as bridge_routes
    from app.modules.bridge.manager import registry

    device_id = "dev-gen-stale"
    _register_trusted(device_id)
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: FakeGatewayWS()})

    client.post(f"/admin/devices/{device_id}/adb/open", headers=ADMIN_HEADERS)
    bridge = registry.active[device_id]

    # 人为构造"旧世代 nonce"：把在册世代推进一格，pending 记录退回一世代
    stale_nonce = "stale-nonce"
    monkeypatch.setattr(bridge_routes.registry, "_generation_by_device", {device_id: bridge.generation + 1})
    monkeypatch.setattr(bridge, "generation", bridge.generation + 1)
    registry.pending[device_id] = (bridge.generation - 1, stale_nonce)

    with (
        pytest.raises(WebSocketDisconnect) as stale,
        client.websocket_connect(f"/gateway/bridge?device_id={device_id}&nonce={stale_nonce}") as ws,
    ):
        ws.receive_bytes()

    assert stale.value.code == 4409


def test_bridge_open_is_idempotent_while_connected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """桥已接通设备时重复 open：返回同一端口并标记 already_open。"""
    from app.modules.bridge.manager import registry

    device_id = "dev-already-open"
    _register_trusted(device_id)
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: FakeGatewayWS()})

    first = client.post(f"/admin/devices/{device_id}/adb/open", headers=ADMIN_HEADERS).json()
    registry.active[device_id].device_ws = object()  # 模拟设备 WS 已接入

    second = client.post(f"/admin/devices/{device_id}/adb/open", headers=ADMIN_HEADERS).json()

    assert second["already_open"] is True
    assert second["port"] == first["port"]


# ── 网关链路层：未信任设备与心跳/确认帧 ───────────────────


def _frame(msg_type: str, payload: dict[str, object]) -> str:
    import json
    import uuid

    return json.dumps(
        {"v": 1, "type": msg_type, "id": str(uuid.uuid4()), "ts": "2026-09-30T12:00:00.000Z", "payload": payload}
    )


def test_gateway_ignores_business_frames_from_untrusted_device(client: TestClient) -> None:
    """未信任设备可保持连接，但业务帧被静默忽略——链路层仍工作。"""
    import json

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(
            _frame(
                "link.hello",
                {
                    "device_id": "dev-untrusted-1",
                    "app_version": "0.3.2",
                    "version_code": 9,
                    "os_version": "4.4.4",
                    "capabilities": [],
                },
            )
        )
        ack = json.loads(ws.receive_text())
        assert ack["payload"]["trusted"] is False

        # 业务帧：不产生任何回帧（未被信任）
        ws.send_text(_frame("door.snapshot", {"doors": [{"door_no": 1, "status_raw": 0}]}))
        ws.send_text(_frame("session.command_result", {"status": "ok"}))

        # 下一条回帧应当是 ping 的 pong，证明前面两帧确实被忽略
        ws.send_text(_frame("link.ping", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        pong = json.loads(ws.receive_text())
        assert pong["type"] == "link.pong"


def test_gateway_handles_link_layer_frames(client: TestClient) -> None:
    """pong 驱动时钟校准、config.ack 回填挂起推送：均不产生回帧，也不抛异常。"""
    import json

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(
            _frame(
                "link.hello",
                {
                    "device_id": "dev-link-1",
                    "app_version": "0.3.2",
                    "version_code": 9,
                    "os_version": "4.4.4",
                    "capabilities": [],
                },
            )
        )
        ws.receive_text()

        ws.send_text(_frame("link.pong", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        ws.send_text(_frame("config.ack", {"revision": 1}))

        ws.send_text(_frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        assert json.loads(ws.receive_text())["type"] == "link.pong"

    from app.core import device_clock

    # pong 已被消化为时钟偏差观测值（非零即说明走通了 update_skew）
    assert device_clock.get_skew("dev-link-1") != 0.0


# ── OTA 管理端点 ──────────────────────────────────────────


def test_ota_rearm_and_delete_release(client: TestClient) -> None:
    """rearm 无确认记录时返回 False；下架返回 True，重复下架返回 False。"""
    files = {"file": ("ntu-shower-0.3.1.apk", io.BytesIO(b"PK"), "application/octet-stream")}
    client.post(
        "/admin/releases",
        headers=ADMIN_HEADERS,
        data={"version": "0.3.1", "version_code": 8, "mandatory": "false", "force": "false", "target": "all"},
        files=files,
    )

    rearm = client.post("/admin/releases/0.3.1/rearm/dev-none", headers=ADMIN_HEADERS).json()
    assert rearm["rearmed"] is False

    assert client.delete("/admin/releases/0.3.1", headers=ADMIN_HEADERS).json()["deleted"] is True
    assert client.delete("/admin/releases/0.3.1", headers=ADMIN_HEADERS).json()["deleted"] is False
