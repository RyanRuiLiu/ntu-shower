"""设备 WS 网关：首帧 hello、坏帧、未知类型、未知 command_id、坏载荷、信任流程与握手内容。"""

import asyncio
import contextlib
import io
import json
import time
from contextlib import AbstractContextManager, contextmanager
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect
from tests import apk, device_link, frames

from app.contracts import DeviceLogKind
from app.core import events
from app.core.clock import now_rfc3339, rfc3339_ago
from app.db.session import session_scope
from app.modules.devices import crud as devices_store
from app.modules.devices.models import Device, DeviceLinkEvent, DeviceLog
from app.modules.devices.schemas import LINK_OFFLINE, LINK_ONLINE


def test_first_frame_must_be_hello(client: TestClient, fresh_db: str) -> None:
    """首帧不是 link.hello：回 proto.hello_required 并断开。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.hello_required"


def test_hello_with_wrong_protocol_version_is_rejected(client: TestClient, fresh_db: str) -> None:
    """hello 携带其他协议版本：回 proto.version_mismatch 并断开（1002）。

    信封版本字段因此有了运行时语义——它不再是"登记了但永不触发"的死码。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-wrong-version", v=2))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.version_mismatch"


def test_retired_device_handshake_is_rejected(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """已停用设备握手即拒：回 device.retired 并按应用私用码关闭（4403）。

    停用的意义就在这里：设备还开着旧 APK 也收不上来数据——hello 在注册档案
    之前就被拒，业务帧永远进不来。4403（RFC 6455 §7.4.2 私用段，两端约定）让
    设备停止重连；1008 的"清凭据重注册"救不回一个被除名的设备。
    """
    device_link.register_trusted_device("dev-retired")
    r = client.patch("/api/v1/devices/dev-retired", headers=admin_headers, json={"retired": True})
    assert r.status_code == 200

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-retired"))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "device.retired"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4403


def test_frame_with_wrong_protocol_version_closes_the_link(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """会话中途出现异版本帧：回错后关闭连接，不静默丢帧。

    静默丢帧会让"连着但什么都不动"——比明确拒绝更难排查。
    """
    with device_link.trusted_online(client, "dev-version-frame", admin_headers) as ws:
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}, v=2))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.version_mismatch"
        assert err["payload"]["message"].startswith("服务端协议版本为 1")


def test_bad_json_gets_structured_error(client: TestClient, fresh_db: str) -> None:
    """非 JSON 帧回 proto.bad_json，连接保持。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-frames-bad"))
        frames.recv_until(ws, "link.hello_ack")

        ws.send_text("not-json")
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.bad_json"

        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"


def test_unknown_type_gets_error_but_stays_connected(client: TestClient, fresh_db: str) -> None:
    """未知 type 回结构化错误但不断开：协议的前向扩展点。

    设备主动发的 link.ping 同样按未知类型回错——link.ping 方向已收窄为
    server→device（设备判活走 WS 协议层 ping，不占应用帧）。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-frames-unknown"))
        frames.recv_until(ws, "link.hello_ack")

        ws.send_text(frames.frame("made.up", {}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:02.000Z"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"


def test_device_pong_updates_clock_skew(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """设备回 pong 时带上自己的墙上时刻，服务端据此算出钟偏差并落库。

    偏差（符号是「服务端时刻 − 设备时刻」）是把帧里的 `open_time` / `in_time` 平移到
    服务端时间轴的依据——那两个字段是"这次会话从什么时候开始"的唯一线索，而设备钟的
    绝对值不可信（自然漂移 8.5 秒/天、还会被人为回拨一小时）。

    断言用相对量：设备报"此刻"，偏差就贴近 0；设备报"两分钟前"，偏差就贴近 120 秒。
    绝对值取决于测试运行的真实时刻，那是控制不了的。
    """
    device_id = "dev-pong-skew"
    with device_link.trusted_online(client, device_id, admin_headers) as ws:
        ws.send_text(frames.frame("link.pong", {"device_time": now_rfc3339()}))
        _settle(ws)
        skew_now = _skew(device_id)
        assert abs(skew_now) < 5, "设备报的就是此刻，两边差不出几秒"

        # 相隔两分钟：超过跳变阈值，直接采信新值而不做 EMA
        ws.send_text(frames.frame("link.pong", {"device_time": rfc3339_ago(120)}))
        _settle(ws)
        skew_past = _skew(device_id)
        assert skew_past > 100, "设备报的是两分钟前，偏差随之变大"
        assert skew_past > skew_now, "落库的确实是新算出来的那个"


def _settle(ws: WebSocketTestSession) -> None:
    """等前面发出的帧被处理完。

    `send_text` 是发出去就返回的，服务端在另一个线程里处理——不设同步点的话，紧接着读库
    很可能读到的是处理前的值。帧在同一连接上是顺序处理的，所以一帧等到它的回帧，
    就说明之前那些帧都已经走完了。同步用设备发的 link.ping：它已不是协议的一部分
    （方向收窄为 server→device），服务端按未知类型回错——回帧必然到来且不断开。
    """
    ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
    err = frames.recv_until(ws, "link.error")
    assert err["payload"]["code"] == "proto.unknown_type"


def test_device_pong_without_device_time_keeps_the_previous_skew(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """pong 没带时刻（或带了个坏值）时偏差不动——一次坏观测不该污染已经稳住的值。"""
    device_id = "dev-pong-no-time"
    with device_link.trusted_online(client, device_id, admin_headers) as ws:
        ws.send_text(frames.frame("link.pong", {"device_time": now_rfc3339()}))
        _settle(ws)
        settled = _skew(device_id)

        ws.send_text(frames.frame("link.pong", {}))
        ws.send_text(frames.frame("link.pong", {"device_time": "not-a-time"}))
        _settle(ws)

    assert _skew(device_id) == settled


def test_unknown_command_id_replies_error(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """回执找不到对应的在途命令：回 command.unknown_id 保持协议反馈。"""
    with device_link.trusted_online(client, "dev-frames-cmd", admin_headers) as ws:
        ws.send_text(frames.frame("device.command_result", {"status": "ok"}, reply_to="no-such-command"))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "command.unknown_id"


def test_ota_progress_is_received_not_rejected(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设备自更新进度帧被接收并记日志，不再回 proto.unknown_type。

    设备端 AgentService 在升级过程中每次进度都发 ota.progress；协议有 Schema 而此前
    服务端没有分支，每帧进度都收到错误帧。修复后只记日志——进度是一次性中间态，
    "是否已安装"由 hello 的 version_code 回答；installing 一帧通知电源计划的布防
    可见性（随后的进程重启掉线是预期表现，豁免告警）。
    """
    from app.gateway import routes as gateway_routes
    from app.modules.power import service as power_service

    seen: list[dict[str, str]] = []

    def capture(_level: str, key: str, _message: str, **kwargs: str) -> None:
        if key == "ota.progress-reported":
            seen.append({name: str(kwargs[name]) for name in ("device_id", "version", "stage")})

    monkeypatch.setattr(gateway_routes, "log_event", capture)
    with device_link.trusted_online(client, "dev-ota-progress", admin_headers) as ws:
        ws.send_text(frames.frame("ota.progress", {"version": "0.6.6", "stage": "downloading", "detail": "1/3"}))
        _settle(ws)
        assert power_service._install_reboot_at == {}
        ws.send_text(frames.frame("ota.progress", {"version": "0.6.6", "stage": "installing"}))
        _settle(ws)

    assert seen == [
        {"device_id": "dev-ota-progress", "version": "0.6.6", "stage": "downloading"},
        {"device_id": "dev-ota-progress", "version": "0.6.6", "stage": "installing"},
    ]
    assert "dev-ota-progress" in power_service._install_reboot_at


def test_unparsable_snapshot_is_acknowledged(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """坏载荷照收回执：派生跳过即可。

    回错误会让设备端不删发件箱里那一行、把队列堵死，而它对内容能否解析无能为力——
    设备端只管送达，判断是服务端的事。
    """
    with device_link.trusted_online(client, "dev-bad-snap", admin_headers) as ws:
        ws.send_text(frames.frame("door.snapshot", {"snapshot_id": "bad-1", "doors": "not-a-list"}))
        ack = frames.recv_until(ws, "door.snapshot_ack")
        assert ack["payload"]["snapshot_id"] == "bad-1"


def test_valid_snapshot_gets_ack(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """已信任设备上报一帧 → door.snapshot_ack 带回原 snapshot_id。"""
    snapshot_id = "11111111-1111-4111-8111-111111111111"
    with device_link.trusted_online(client, "dev-snap-ack", admin_headers) as ws:
        ws.send_text(frames.frame("door.snapshot", {"snapshot_id": snapshot_id, "doors": [{"door_no": 1}]}))
        ack = frames.recv_until(ws, "door.snapshot_ack")
        assert ack["payload"]["snapshot_id"] == snapshot_id


def test_untrusted_device_business_frames_are_ignored(client: TestClient, fresh_db: str) -> None:
    """未信任设备可保持连接，但业务帧被静默忽略，链路层照常工作。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-untrusted-1"))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False

        ws.send_text(frames.frame("door.snapshot", {"doors": [{"door_no": 1}]}))
        ws.send_text(frames.frame("device.command_result", {"status": "ok"}))

        # 下一条回帧是 ping 的错误回帧，证明前面两帧确实被忽略（连接未断）
        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:00.000Z"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"


def test_device_initiated_ping_gets_unknown_type(client: TestClient, fresh_db: str) -> None:
    """设备主动发 link.ping：按未知类型回错，连接保持。

    心跳方向在两端约定里只有 server→device（判活 + 时间锚点都由那一侧承担）；
    设备侧判活走 WS 协议层 ping（OkHttp pingInterval），不占应用帧。旧版设备
    仍会发 ping——回错而不是断开，等 OTA 收敛。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-link-1"))
        frames.recv_until(ws, "link.hello_ack")

        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

        # 连接仍然活着：后续业务帧照常受理
        ws.send_text(frames.frame("made.up", {}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"


def test_trust_flow_and_device_listing(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """待认领 → 信任 → 上线：信任位热生效并出现在运维档案里。"""
    device_id = "test-device-0001"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False
        assert ack["payload"]["config"]["notice_text_size_sp"] == 24
        # 只给设备端消费的键——服务端自己的键不占设备的解析路径。这条与 `config.update`
        # 那条路必须一致，否则设备连上与连上之后看到的键集不同。
        assert "renew_wait_students" not in ack["payload"]["config"]
        assert "renew_wait_enabled" not in ack["payload"]["config"]

        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
        frames.recv_until(ws, "link.error")

    patched = client.patch(f"/api/v1/devices/{device_id}", headers=admin_headers, json={"trusted": True})
    assert patched.status_code == 200
    assert patched.json()["trusted"] is True

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is True

        # 在线状态以内存连接表为准，须在连接保持期间查询
        listed = client.get(f"/api/v1/devices/{device_id}", headers=admin_headers).json()
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
        files={"file": ("ntu-shower-0.3.0.apk", io.BytesIO(apk.build_apk("0.3.0", 6)), "application/octet-stream")},
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id, version_code=5))
        ack = frames.recv_until(ws, "link.hello_ack")
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
        files={"file": ("ntu-shower-0.3.1.apk", io.BytesIO(apk.build_apk("0.3.1", 8)), "application/octet-stream")},
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id, version_code=8))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["upgrade"] is None

    assert _ota_call("is_installed", device_id, "0.3.1") is True


def test_server_ping_refreshes_last_seen(client: TestClient, fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """服务端 ping 循环刷新 last_seen_at：设备对 ping 的 pong 应答是服务端眼里的
    存活证据（touch_seen）；先改成显然过期的值再验证推进。"""
    from app.gateway import routes as gateway_routes
    from app.modules.devices import service as devices_service

    device_id = "dev-heartbeat"
    monkeypatch.setattr(gateway_routes, "PING_INTERVAL_S", 0.2)
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        frames.recv_until(ws, "link.hello_ack")

        from datetime import UTC, datetime

        from app.modules.devices import crud as devices_store

        with _svc_session() as session:
            devices_store.touch_seen(session, device_id, datetime(2020, 1, 1, tzinfo=UTC))

        # pong 落地后的下一个入站信号触发帧处理完成：等下一个服务端 ping 到来，
        # 立即回 pong，再发一帧设备 ping 等未知类型回帧——此时 pong 的 touch_seen
        # 必已按序执行完（帧在同一连接上顺序处理），读库一次即可，不轮询
        frames.recv_until(ws, "link.ping")
        ws.send_text(frames.frame("link.pong", {"device_time": now_rfc3339()}))
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

        with _svc_session() as session:
            record = devices_service.get_device(session, device_id)
        assert record is not None
        assert record["last_seen_at"] > "2020-01-01T00:00:00.000Z"


def _next_published_update() -> dict[str, Any]:
    """在本线程的事件循环里订阅事件总线，发布一次 device.update 并取回消息。"""
    from app.modules.devices import service as devices_service

    async def flow() -> dict[str, Any]:
        sid, queue = events.subscribe()
        with _svc_session() as session:
            devices_service.publish_device_update(session, "dev-update-1")
        await asyncio.sleep(0)
        raw = queue.get_nowait()
        events.unsubscribe(sid)
        return dict(json.loads(raw))

    return asyncio.run(flow())


def test_device_update_push_is_trimmed_summary(fresh_db: str) -> None:
    """device.update 推送是精简档案：IP、版本、时钟偏差等运维字段不进公开推送。"""

    from app.db.base import utcnow
    from app.modules.devices import crud as devices_store

    with device_link.db() as session:
        devices_store.register(
            session,
            device_id="dev-update-1",
            app_version="0.3.33",
            version_code=40,
            os_version="4.4.4",
            capabilities=["session.assign"],
            ip="10.0.0.9",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )

    message = _next_published_update()

    assert message["type"] == "device.update"
    payload = message["payload"]
    assert set(payload) == {"device_id", "name", "online", "retired", "at"}
    assert payload["online"] is False


def test_device_update_with_missing_record_keeps_minimal_payload(fresh_db: str) -> None:
    """设备记录查不到时推最小占位，推送不因查询失败而中断。"""

    async def flow() -> None:
        sid, queue = events.subscribe()
        from app.modules.devices import service as devices_service

        with _svc_session() as session:
            devices_service.publish_device_update(session, "dev-never-registered")
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
    from app.modules.devices import service

    device_id = "dev-online-src"
    device_link.register_device_raw(device_id)

    with _svc_session() as session:
        record = service.get_device(session, device_id)
        assert record is not None
        assert record["online"] is False
        listed = next(item for item in service.list_devices(session) if item["device_id"] == device_id)
        assert listed["online"] is False

        manager.register(device_id, cast(WebSocket, object()))
        assert service.get_device(session, device_id)["online"] is True
        manager.unregister(device_id, cast(WebSocket, object()))  # 不是同一条连接，按设计不动
        assert service.get_device(session, device_id)["online"] is True


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
        ws.send_text(frames.hello_frame("dev-gw-boom"))
        ws.send_text(frames.frame("link.ping", {"sender_time": "2026-09-30T12:00:01.000Z"}))
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


def test_anchor_loop_sends_periodic_pings_and_keeps_the_link(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锚点循环按周期发 link.ping（唯一职责：时间锚点刷新），无论是否应答。

    链路判活不在此列：服务端由 uvicorn 的 WS 协议层 ping 承担（默认 20 秒、
    pong 超时断连），设备端由 OkHttp pingInterval 承担——app 层不再重复判活，
    不回 pong 的连接也保持打开。
    """
    # 间隔给 0.2 秒：两个完整周期亚秒级跑完，行为与真实间隔无关
    monkeypatch.setattr("app.gateway.routes.PING_INTERVAL_S", 0.2)
    device_id = "dev-anchor-loop"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        frames.recv_until(ws, "link.hello_ack")

        ping1 = frames.recv_until(ws, "link.ping")
        ws.send_text(frames.frame("link.pong", {"device_time": now_rfc3339()}, reply_to=ping1["id"]))
        frames.recv_until(ws, "link.ping")  # 回了 pong：下一个周期照常锚定

        # 不回 pong：链路保持，锚点照常下发（判活不在此列）
        frames.recv_until(ws, "link.ping")


def test_anchor_loop_closes_a_dead_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """ping 发送失败（TCP 静默丢包后的死连接）：主动关闭让 handler 的 receive_text() 解除阻塞。"""
    from app.gateway import routes as gateway_routes

    monkeypatch.setattr(gateway_routes, "PING_INTERVAL_S", 0.01)
    closed: dict[str, int | None] = {}

    class _DeadLink:
        async def send_text(self, _text: str) -> None:
            message = "link is dead"
            raise RuntimeError(message)

        async def close(self, code: int | None = None) -> None:
            closed["code"] = code

    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        gateway_routes, "log_event", lambda _l, event, _m, **attrs: events.append({"event": event, **attrs})
    )

    asyncio.run(gateway_routes._anchor_ping_loop(_DeadLink(), "dev-anchor-dead"))

    assert closed["code"] == gateway_routes.WS_CLOSE_GOING_AWAY
    assert any(record["event"] == "gateway.ping-loop-ended" for record in events)


def test_link_events_record_connect_and_disconnect(client: TestClient, fresh_db: str) -> None:
    """设备上线与下线各留一条记录。

    帧是**事件驱动**的（状态变化才上报，空闲时不上报），所以"没消息"既可能是"没人用"
    也可能是"设备离线"——两者在数据里长得一模一样、事后无法区分。这张表是唯一的分辨
    依据。
    """
    device_id = "dev-link-events"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        frames.recv_until(ws, "link.hello_ack")

    # 关闭连接只是把"关闭帧"交给服务端，offline 由服务端那条连接的 finally 异步记下。
    # 测试线程不等它跑完就读库会偶发地只看到 online；等结果出现即可，不掩盖任何产品路径。
    deadline = time.monotonic() + 2
    while True:
        with session_scope() as session:
            events = list(
                session.scalars(
                    select(DeviceLinkEvent.event)
                    .join(Device, Device.id == DeviceLinkEvent.device_id)
                    .where(Device.id == device_id)
                    .order_by(DeviceLinkEvent.id)
                )
            )
        if events == [LINK_ONLINE, LINK_OFFLINE] or time.monotonic() > deadline:
            break
        # 只让出执行权：结果由服务端线程写入，读库本身不产生等待
        time.sleep(0)
    assert events == [LINK_ONLINE, LINK_OFFLINE]


def test_reconnect_does_not_record_a_false_offline(client: TestClient, fresh_db: str) -> None:
    """重连顶替旧连接时，不记假的 offline。

    新连接顶替旧连接之后，被顶替的那条才走收尾。那一刻设备是在线的——记 offline 会让
    序列变成 online→online→offline，而最后那个 offline 是假的。实测这台设备一天重连
    两百次上下，假 offline 会占掉绝大多数。
    """
    device_id = "dev-reconnect"
    with client.websocket_connect("/gateway/device") as first:
        first.send_text(frames.hello_frame(device_id))
        frames.recv_until(first, "link.hello_ack")

        # 第二条连接顶替第一条，此时设备仍在线
        with client.websocket_connect("/gateway/device") as second:
            second.send_text(frames.hello_frame(device_id))
            frames.recv_until(second, "link.hello_ack")

    with session_scope() as session:
        events = list(
            session.scalars(
                select(DeviceLinkEvent.event)
                .join(Device, Device.id == DeviceLinkEvent.device_id)
                .where(Device.id == device_id)
                .order_by(DeviceLinkEvent.id)
            )
        )
    assert events.count(LINK_ONLINE) == 2
    # 两条连接都收尾了，但设备全程在线——只有最后真正断开那一次才算
    assert events.count(LINK_OFFLINE) == 1


def test_marking_trusted_kicks_the_live_connection(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """改信任位必须断开现有连接，否则设备带着旧信任持续失效。

    信任只在 hello 握手时判定一次：先连接、后标记信任的设备，业务帧与命令结果会被
    当作未信任静默丢弃——首台新设备入网实测「设备在线、功能全坏」。断开后设备按
    重连退避几秒内带着新信任回来。
    """
    device_id = "dev-kick-on-trust"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False

        resp = client.patch(f"/api/v1/devices/{device_id}", json={"trusted": True}, headers=admin_headers)
        assert resp.status_code == 200

        def _read_until_closed() -> None:
            while True:
                ws.receive_text()

        with pytest.raises(WebSocketDisconnect):
            _read_until_closed()


def test_device_log_upload_is_stored_and_queryable(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """设备日志上送：握手后设备把日志尾部送来，服务端按（设备，种类）保留。

    这是「设备离线期间发生了什么」的排障通道：证据在回连那一刻自动到位，
    现场不再需要读文件。
    """
    with device_link.trusted_online(client, "dev-log-upload", admin_headers) as ws:
        ws.send_text(frames.frame("device.log_upload", {"kind": "device", "content": "line1\nline2\n"}))
        ws.send_text(frames.frame("device.log_upload", {"kind": "crash", "content": "boom\n"}))
        ws.send_text(frames.frame("device.log_upload", {"kind": "ota", "content": "Success\n"}))
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        frames.recv_until(ws, "link.error")

    device = "dev-log-upload"
    r = client.get(f"/api/v1/devices/{device}/logs?kind=device", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["content"] == "line1\nline2\n"
    assert r.json()["uploaded_at"]

    r = client.get(f"/api/v1/devices/{device}/logs?kind=crash", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["content"] == "boom\n"

    r = client.get(f"/api/v1/devices/{device}/logs?kind=ota", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["content"] == "Success\n"


def test_device_log_upload_rejects_oversized_content(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """日志上送有 64 KiB 上限：超限回 link.error，不写入数据库。"""
    with device_link.trusted_online(client, "dev-log-too-large", admin_headers) as ws:
        ws.send_text(frames.frame("device.log_upload", {"kind": "device", "content": "x" * (64 * 1024 + 1)}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.bad_json"
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        frames.recv_until(ws, "link.error")

    assert client.get("/api/v1/devices/dev-log-too-large/logs?kind=device", headers=admin_headers).status_code == 404


def test_device_log_upload_requires_trusted_and_valid_kind(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """未信任设备的日志上送被静默忽略；坏种类回 link.error 保持协议反馈。"""
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-log-untrusted"))
        frames.recv_until(ws, "link.hello_ack")
        ws.send_text(frames.frame("device.log_upload", {"kind": "device", "content": "x"}))
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

    with device_link.trusted_online(client, "dev-log-badkind", admin_headers) as ws:
        ws.send_text(frames.frame("device.log_upload", {"kind": "nope", "content": "x"}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.bad_json"

    r = client.get("/api/v1/devices/dev-log-untrusted/logs?kind=device", headers=admin_headers)
    assert r.status_code == 404


def test_device_log_upload_keeps_only_recent_per_kind(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """同一（设备，种类）只留最近几份：上送是现态读数，不是无限队列。"""
    with device_link.trusted_online(client, "dev-log-trim", admin_headers) as ws:
        for round_no in range(7):
            ws.send_text(frames.frame("device.log_upload", {"kind": "device", "content": f"round-{round_no}"}))
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        frames.recv_until(ws, "link.error")

    body = client.get("/api/v1/devices/dev-log-trim/logs?kind=device", headers=admin_headers).json()
    assert body["content"] == "round-6"
    with session_scope() as session:
        logs_n = session.scalar(
            select(func.count())
            .select_from(DeviceLog)
            .join(Device, Device.id == DeviceLog.device_id)
            .where(Device.id == "dev-log-trim", DeviceLog.kind == DeviceLogKind.DEVICE)
        )
    assert logs_n == 5


@contextmanager
def _svc_session() -> "AbstractContextManager[Session]":
    """直调 service 层的短会话。"""
    from app.db.session import session_scope

    with session_scope() as session:
        yield session


def _ota_call(name: str, *args: object) -> object:
    """OTA 持久化直调：短会话。"""
    from app.modules.ota import crud as ota_store

    fn = getattr(ota_store, name)
    with _svc_session() as session:
        return fn(session, *args)


def _skew(device_id: str) -> float:
    """读设备的持久化钟偏差。"""
    from app.modules.devices import crud as devices_store

    with _svc_session() as session:
        return devices_store.get_clock_skew(session, device_id)


def test_device_status_frame_lands_power_facts(client: TestClient, fresh_db: str) -> None:
    """device.status 帧：上报事实落影子行；形状不对回结构化错误且连接保持。"""
    device_link.register_trusted_device("dev-status-frame")
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame("dev-status-frame"))
        frames.recv_until(ws, "link.hello_ack")

        ws.send_text(
            frames.frame(
                "device.status",
                {"auto_close_time": "23:58", "auto_open_time": "10:55", "zone_offset_minutes": 480},
            )
        )

        # WS 帧按序处理：后发的 ping 收到未知类型回帧即证明 status 帧已处理完
        ws.send_text(frames.frame("link.ping", {"sender_time": now_rfc3339()}))
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

        with session_scope() as session:
            assert devices_store.power_facts(session, "dev-status-frame") == ("23:58", "10:55", 480)

        ws.send_text(
            frames.frame(
                "device.status",
                {"auto_close_time": "99:99", "auto_open_time": "10:55", "zone_offset_minutes": 480},
            )
        )
        err = frames.recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.bad_json"
