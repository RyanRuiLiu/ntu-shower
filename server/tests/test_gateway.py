"""设备 WS 网关：首帧 hello、坏帧、未知类型、未知 command_id、坏载荷、信任流程与握手内容。"""

import asyncio
import contextlib
import io
import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from tests import helpers

from app.core import db, events
from app.core.clock import now_rfc3339, rfc3339_ago
from app.modules.devices import repository as devices_repository
from app.modules.devices.schemas import LINK_OFFLINE, LINK_ONLINE
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


def test_device_pong_updates_clock_skew(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """设备回 pong 时带上自己的墙上时刻，服务端据此算出钟偏差并落库。

    偏差（符号是「服务端时刻 − 设备时刻」）是把帧里的 `open_time` / `in_time` 平移到
    服务端时间轴的依据——那两个字段是"这次会话从什么时候开始"的唯一线索，而设备钟的
    绝对值不可信（自然漂移 8.5 秒/天、还会被人为回拨一小时）。

    断言用相对量：设备报"此刻"，偏差就贴近 0；设备报"两分钟前"，偏差就贴近 120 秒。
    绝对值取决于测试运行的真实时刻，那是控制不了的。
    """
    device_id = "dev-pong-skew"
    with helpers.trusted_online(client, device_id, admin_headers) as ws:
        ws.send_text(helpers.frame("link.pong", {"device_time": now_rfc3339()}))
        _settle(ws)
        skew_now = devices_repository.get_clock_skew(device_id)
        assert abs(skew_now) < 5, "设备报的就是此刻，两边差不出几秒"

        # 相隔两分钟：超过跳变阈值，直接采信新值而不做 EMA
        ws.send_text(helpers.frame("link.pong", {"device_time": rfc3339_ago(120)}))
        _settle(ws)
        skew_past = devices_repository.get_clock_skew(device_id)
        assert skew_past > 100, "设备报的是两分钟前，偏差随之变大"
        assert skew_past > skew_now, "落库的确实是新算出来的那个"


def _settle(ws: WebSocketTestSession) -> None:
    """等前面发出的帧被处理完。

    `send_text` 是发出去就返回的，服务端在另一个线程里处理——不设同步点的话，紧接着读库
    很可能读到的是处理前的值。帧在同一连接上是顺序处理的，所以一帧 ping 等到它的 pong，
    就说明之前那些帧都已经走完了。
    """
    ws.send_text(helpers.frame("link.ping", {"sender_time": now_rfc3339()}))
    helpers.recv_until(ws, "link.pong")


def test_device_pong_without_device_time_keeps_the_previous_skew(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """pong 没带时刻（或带了个坏值）时偏差不动——一次坏观测不该污染已经稳住的值。"""
    device_id = "dev-pong-no-time"
    with helpers.trusted_online(client, device_id, admin_headers) as ws:
        ws.send_text(helpers.frame("link.pong", {"device_time": now_rfc3339()}))
        _settle(ws)
        settled = devices_repository.get_clock_skew(device_id)

        ws.send_text(helpers.frame("link.pong", {}))
        ws.send_text(helpers.frame("link.pong", {"device_time": "not-a-time"}))
        _settle(ws)

    assert devices_repository.get_clock_skew(device_id) == settled


def test_unknown_command_id_replies_error(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """回执找不到对应的在途命令：回 command.unknown_id 保持协议反馈。"""
    with helpers.trusted_online(client, "dev-frames-cmd", admin_headers) as ws:
        ws.send_text(helpers.frame("session.command_result", {"status": "ok"}, reply_to="no-such-command"))
        err = helpers.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "command.unknown_id"


def test_unparsable_snapshot_is_acknowledged_and_recorded(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """坏载荷照收回执，并如实落进原始层。

    回错误会让设备端不删发件箱里那一行、把队列堵死，而它对内容能否解析无能为力——
    设备端只管送达，判断是服务端的事。原始层留下"设备报过这么一帧"，派生跳过即可。
    """
    with helpers.trusted_online(client, "dev-bad-snap", admin_headers) as ws:
        ws.send_text(helpers.frame("door.snapshot", {"snapshot_id": "bad-1", "doors": "not-a-list"}))
        ack = helpers.recv_until(ws, "door.snapshot_ack")
        assert ack["payload"]["snapshot_id"] == "bad-1"

    rows = db.get_conn().execute("SELECT doors FROM door_snapshot WHERE snapshot_id = ?", ("bad-1",)).fetchall()
    assert [row["doors"] for row in rows] == ["[]"]


def test_valid_snapshot_gets_ack(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """已信任设备上报一帧 → door.snapshot_ack 带回原 snapshot_id。"""
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


def test_link_roundtrip_replies_empty_pong(client: TestClient, fresh_db: str) -> None:
    """ping/pong 回路工作：pong 是回执，载荷为空。

    设备的时间锚点取自服务端发来的 ping，反方向不需要任何信息——pong 只回答
    "我还活着"，判活用它，时刻不从它取。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame("dev-link-1"))
        helpers.recv_until(ws, "link.hello_ack")

        ws.send_text(helpers.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        pong = helpers.recv_until(ws, "link.pong")
        assert pong["reply_to"]
        assert pong["payload"] == {}


def test_trust_flow_and_device_listing(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """待认领 → 信任 → 上线：信任位热生效并出现在运维档案里。"""
    device_id = "test-device-0001"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        ack = helpers.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False
        assert ack["payload"]["config"]["heartbeat_interval_s"] == 30
        # 只给设备端消费的键——服务端自己的键不占设备的解析路径。这条与 `config.update`
        # 那条路必须一致，否则设备连上与连上之后看到的键集不同。
        assert "renew_wait_students" not in ack["payload"]["config"]
        assert "auto_settle_pause_threshold_s" not in ack["payload"]["config"]

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

    # 间隔给 0.2 秒：两个完整周期亚秒级跑完，行为与真实间隔无关
    monkeypatch.setattr("app.modules.config.store.effective", lambda _device_id: {"ping_interval_s": 0.2})
    device_id = "dev-ping-loop"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(ws, "link.hello_ack")

        ping1 = helpers.recv_until(ws, "link.ping")
        ws.send_text(helpers.frame("link.pong", {"device_time": now_rfc3339()}, reply_to=ping1["id"]))
        helpers.recv_until(ws, "link.ping")  # 回了 pong：下一个周期照常探测

        # 不再回 pong：一个完整周期后服务端主动关闭链路
        def _recv_until_closed() -> None:
            while True:
                ws.receive_text()

        with pytest.raises(WebSocketDisconnect):
            _recv_until_closed()


def test_link_events_record_connect_and_disconnect(client: TestClient, fresh_db: str) -> None:
    """设备上线与下线各留一条记录。

    帧是**事件驱动**的（状态变化才上报，空闲时不上报），所以"没消息"既可能是"没人用"
    也可能是"设备离线"——两者在数据里长得一模一样、事后无法区分。这张表是唯一的分辨
    依据。
    """
    device_id = "dev-link-events"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(ws, "link.hello_ack")

    rows = (
        db.get_conn()
        .execute("SELECT event FROM device_link_event WHERE device_id = ? ORDER BY id", (device_id,))
        .fetchall()
    )
    assert [row["event"] for row in rows] == [LINK_ONLINE, LINK_OFFLINE]


def test_reconnect_does_not_record_a_false_offline(client: TestClient, fresh_db: str) -> None:
    """重连顶替旧连接时，不记假的 offline。

    新连接顶替旧连接之后，被顶替的那条才走收尾。那一刻设备是在线的——记 offline 会让
    序列变成 online→online→offline，而最后那个 offline 是假的。实测这台设备一天重连
    两百次上下，假 offline 会占掉绝大多数。
    """
    device_id = "dev-reconnect"
    with client.websocket_connect("/gateway/device") as first:
        first.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(first, "link.hello_ack")

        # 第二条连接顶替第一条，此时设备仍在线
        with client.websocket_connect("/gateway/device") as second:
            second.send_text(helpers.hello_frame(device_id))
            helpers.recv_until(second, "link.hello_ack")

    events = [
        row["event"]
        for row in db.get_conn().execute(
            "SELECT event FROM device_link_event WHERE device_id = ? ORDER BY id", (device_id,)
        )
    ]
    assert events.count(LINK_ONLINE) == 2
    # 两条连接都收尾了，但设备全程在线——只有最后真正断开那一次才算
    assert events.count(LINK_OFFLINE) == 1
