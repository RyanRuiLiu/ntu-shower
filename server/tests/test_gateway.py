"""设备 WS 网关：首帧 hello、坏帧、未知类型、未知 command_id、坏快照、信任流程与握手内容。"""

import asyncio
import contextlib
import io
import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from tests import helpers

from app.core import device_clock, events
from app.modules.ota import repository as ota_repository


def test_first_frame_must_be_hello(client: TestClient, fresh_db: str) -> None:
    """首帧不是 link.hello：回 proto.hello_required 并断开。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.hello_required"


def test_bad_json_gets_structured_error(client: TestClient, fresh_db: str) -> None:
    """非 JSON 帧回 proto.bad_json，连接保持。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame("dev-frames-bad"))
        helpers.recv_until(ws, "link.hello_ack")

        ws.send_text("not-json")
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.bad_json"

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        assert helpers.recv_until(ws, "link.pong")["reply_to"]


def test_unknown_type_gets_error_but_stays_connected(client: TestClient, fresh_db: str) -> None:
    """未知 type 回结构化错误但不断开：协议的前向扩展点。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame("dev-frames-unknown"))
        helpers.recv_until(ws, "link.hello_ack")

        ws.send_text(helpers.frame("made.up", {}))
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:02.000Z"}))
        assert helpers.recv_until(ws, "link.pong")["reply_to"]


def test_unknown_command_id_replies_error(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """回执找不到对应的在途命令：回 command.unknown_id 保持协议反馈。"""
    with helpers.trusted_online(client, "dev-frames-cmd", admin_headers) as ws:
        ws.send_text(helpers.frame("session.command_result", {"status": "ok"}, reply_to="no-such-command"))
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "command.unknown_id"


def test_invalid_snapshot_rejected(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """已信任设备上报坏快照 → link.error(door.invalid_snapshot)。"""
    with helpers.trusted_online(client, "dev-bad-snap", admin_headers) as ws:
        ws.send_text(helpers.frame("door.snapshot", {"snapshot_id": "x", "doors": "not-a-list"}))
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "door.invalid_snapshot"


def test_valid_snapshot_gets_ack(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """已信任设备上报快照 → door.snapshot_ack 带回原 snapshot_id。"""
    snapshot_id = "11111111-1111-4111-8111-111111111111"
    with helpers.trusted_online(client, "dev-snap-ack", admin_headers) as ws:
        ws.send_text(helpers.frame("door.snapshot", {"snapshot_id": snapshot_id, "doors": [{"door_no": 1}]}))
        ack = helpers.recv_until(ws, "door.snapshot_ack")
        assert ack["payload"]["snapshot_id"] == snapshot_id


def test_untrusted_device_business_frames_are_ignored(client: TestClient, fresh_db: str) -> None:
    """未信任设备可保持连接，但业务帧被静默忽略，链路层照常工作。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame("dev-untrusted-1"))
        ack = helpers.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False

        ws.send_text(helpers.frame("door.snapshot", {"doors": [{"door_no": 1}]}))
        ws.send_text(helpers.frame("session.command_result", {"status": "ok"}))

        # 下一条回帧是 ping 的 pong，证明前面两帧确实被忽略
        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        assert helpers.recv_until(ws, "link.pong")["reply_to"]


def test_link_roundtrip_updates_clock_skew(client: TestClient, fresh_db: str) -> None:
    """ping/pong 回路工作，pong 的设备时间戳被消化为时钟偏差观测。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame("dev-link-1"))
        helpers.recv_until(ws, "link.hello_ack")

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        assert helpers.recv_until(ws, "link.pong")["reply_to"]

        ws.send_text(helpers.frame("link.pong", {"sender_time": "2020-01-01T00:00:00.000Z"}))
        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:02.000Z"}))
        helpers.recv_until(ws, "link.pong")

    assert device_clock.get_skew("dev-link-1") != 0.0


def test_trust_flow_and_device_listing(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """待认领 → 信任 → 上线：信任位热生效并出现在运维档案里。"""
    device_id = "test-device-0001"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        ack = helpers.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False
        assert ack["payload"]["config"]["heartbeat_interval_s"] == 30

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        helpers.recv_until(ws, "link.pong")

    patched = client.patch(f"/api/v1/devices/{device_id}", headers=admin_headers, json={"trusted": True})
    assert patched.status_code == 200
    assert patched.json()["trusted"] is True

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        ack = helpers.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is True

        # 在线状态以内存连接表为准，须在连接保持期间查询
        runtime = client.get("/api/v1/devices/runtime", headers=admin_headers).json()
        listed = next(item for item in runtime if item["device_id"] == device_id)
        assert listed["trusted"] is True
        assert listed["online"] is True


def test_handshake_carries_upgrade_for_targeted_release(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """发布新版本后设备重连：握手直接携带 upgrade 元数据。"""
    device_id = "dev-ota-1"
    client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "0.3.0", "version_code": 6, "target": device_id},
        files={"file": ("ntu-shower-0.3.0.apk", io.BytesIO(b"PK"), "application/octet-stream")},
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id, version_code=5))
        ack = helpers.recv_until(ws, "link.hello_ack")
        upgrade = ack["payload"]["upgrade"]
        assert upgrade is not None
        assert upgrade["version"] == "0.3.0"
        assert len(upgrade["sha256"]) == 64


def test_force_release_not_repushed_after_install_confirmation(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """设备已在目标版本时 force 发布不得再次下发（防无限重装循环）。"""
    device_id = "dev-loop-guard"
    client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "0.3.1", "version_code": 8, "force": "true", "target": device_id},
        files={"file": ("ntu-shower-0.3.1.apk", io.BytesIO(b"PK"), "application/octet-stream")},
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id, version_code=8))
        ack = helpers.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["upgrade"] is None

    assert ota_repository.is_installed(device_id, "0.3.1") is True


def test_heartbeat_refreshes_last_seen(client: TestClient, fresh_db: str) -> None:
    """心跳刷新 last_seen_at；先改成显然过期的值再验证推进。"""
    from app.core.db import get_conn
    from app.modules.devices import service as devices_service

    device_id = "dev-heartbeat"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(ws, "link.hello_ack")

        conn = get_conn()
        conn.execute("UPDATE device SET last_seen_at = ? WHERE device_id = ?", ("2020-01-01T00:00:00.000Z", device_id))
        conn.commit()

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        helpers.recv_until(ws, "link.pong")

        record = devices_service.get_device(device_id)
        assert record is not None
        assert record["last_seen_at"] > "2020-01-01T00:00:00.000Z"


def _next_published_update() -> dict[str, Any]:
    """在本线程的事件循环里订阅事件总线，发布一次 device.update 并取回消息。"""
    from app.modules.devices import service as devices_service

    async def flow() -> dict[str, Any]:
        sid, queue = events.subscribe()
        devices_service.publish_device_update("dev-update-1")
        await asyncio.sleep(0)
        raw = queue.get_nowait()
        events.unsubscribe(sid)
        return dict(json.loads(raw))

    return asyncio.run(flow())


def test_device_update_push_is_trimmed_summary(fresh_db: str) -> None:
    """device.update 推送是精简档案：IP、版本、时钟偏差等运维字段不进公开推送。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(
        "dev-update-1", "0.3.33", 40, "4.4.4", ["session.assign"], "10.0.0.9", "2026-06-01T08:00:00.000Z"
    )

    message = _next_published_update()

    assert message["type"] == "device.update"
    payload = message["payload"]
    assert set(payload) == {"device_id", "name", "online", "at"}
    assert payload["online"] is False


def test_device_update_with_missing_record_keeps_minimal_payload(fresh_db: str) -> None:
    """设备记录查不到时推最小占位，推送不因查询失败而中断。"""

    async def flow() -> None:
        sid, queue = events.subscribe()
        from app.modules.devices import service as devices_service

        devices_service.publish_device_update("dev-never-registered")
        await asyncio.sleep(0)
        raw = queue.get_nowait()
        events.unsubscribe(sid)
        message = dict(json.loads(raw))
        assert message["type"] == "device.update"
        assert message["payload"]["device_id"] == "dev-never-registered"
        assert message["payload"]["online"] is False

    asyncio.run(flow())


def test_online_reflects_connection_not_storage(fresh_db: str) -> None:
    """档案在库里、连接不在内存里 → 报离线（进程刚重启后的真实状态）。"""
    from fastapi import WebSocket

    from app.core.connections import manager
    from app.modules.devices import repository, service

    device_id = "dev-online-src"
    repository.register(device_id, "0.3.35", 42, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")

    record = service.get_device(device_id)
    assert record is not None
    assert record["online"] is False
    listed = next(item for item in service.list_devices() if item["device_id"] == device_id)
    assert listed["online"] is False

    manager.register(device_id, cast(WebSocket, object()))
    assert service.get_device(device_id)["online"] is True
    manager.unregister(device_id, cast(WebSocket, object()))  # 不是同一条连接，按设计不动
    assert service.get_device(device_id)["online"] is True


def test_unexpected_frame_error_is_logged(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """帧处理里的未预期异常必须留痕后重新抛出；断开是常态不按异常处理。"""
    from app.gateway import routes as gateway_routes

    records: list[dict[str, Any]] = []

    def spy(level: str, event: str, message: str, **attrs: object) -> None:
        records.append({"level": level, "event": event, **attrs})

    async def boom(*_args: object, **_kwargs: object) -> None:
        message = "frame handler exploded"
        raise RuntimeError(message)

    monkeypatch.setattr(gateway_routes, "log_event", spy)
    monkeypatch.setattr(gateway_routes, "_handle_frame", boom)

    def pump(ws: WebSocketTestSession) -> None:
        ws.send_text(helpers.hello_frame("dev-gw-boom"))
        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        while True:
            ws.receive_text()

    # 异常会穿透到 TestClient；读到连接中断为止，hello_ack 与探测 ping 都排在触发帧之前
    with contextlib.suppress(Exception), client.websocket_connect("/gateway/device") as ws:
        pump(ws)

    assert any(record["event"] == "gateway.session-failed" for record in records)


def test_event_type_names_use_dots() -> None:
    """事件名用点号，与设备协议一致。"""
    assert events.DEVICE_UPDATE == "device.update"
    assert events.DOOR_UPDATE == "door.update"


def test_server_ping_loop_probes_and_enforces_pong(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务端按配置间隔主动 ping：回 pong 则链路保持，一个周期无 pong 则主动关链。"""
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr("app.modules.config.store.effective", lambda _device_id: {"ping_interval_s": 1})
    device_id = "dev-ping-loop"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(ws, "link.hello_ack")

        ping1 = helpers.recv_until(ws, "link.ping")
        ws.send_text(helpers.frame("link.pong", {"sender_time": "2026-09-30T12:00:01.000Z"}, reply_to=ping1["id"]))
        helpers.recv_until(ws, "link.ping")  # 回了 pong：下一个周期照常探测

        # 不再回 pong：一个完整周期后服务端主动关闭链路
        def _recv_until_closed() -> None:
            while True:
                ws.receive_text()

        with pytest.raises(WebSocketDisconnect):
            _recv_until_closed()
