"""网关闭环测试：待认领 → 信任 → 上线正常；心跳回路；未知类型报错。"""

import contextlib
import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


def _hello(device_id: str, version_code: int = 0) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-29T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.1.0",
                "version_code": version_code,
                "os_version": "4.4.4",
                "capabilities": ["session.assign"],
                "uptime_s": 45176,
            },
        }
    )


def _envelope(msg_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": msg_type,
            "id": str(uuid.uuid4()),
            "ts": "2026-09-29T12:00:00.000Z",
            "payload": payload,
        }
    )


def test_untrusted_device_pends_then_trusted(client: TestClient) -> None:
    device_id = "test-device-0001"

    # 1) 未信任设备：连接成功但 trusted=false
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = _recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is False
        assert ack["payload"]["config"]["heartbeat_interval_s"] == 30

        # 2) 心跳回路
        ws.send_text(_envelope("link.ping", {"sender_time": "2026-09-29T12:00:01.000Z"}))
        pong = _recv_until(ws, "link.pong")
        assert pong["reply_to"]

        # 3) 未知类型 → 结构化错误，不断开
        ws.send_text(_envelope("made.up", {}))
        err = _recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.unknown_type"

    # 4) 后台信任（需令牌）
    r = client.patch(f"/ops/devices/{device_id}", headers={"X-Ops-Token": "test-token"}, json={"trusted": True})
    assert r.status_code == 200
    assert r.json()["trusted"] is True

    # 5) 错误令牌被拒
    r = client.get("/ops/devices", headers={"X-Ops-Token": "wrong"})
    assert r.status_code == 401

    # 6) 重新连接：trusted=true，出现在设备列表
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = _recv_until(ws, "link.hello_ack")
        assert ack["payload"]["trusted"] is True
        r = client.get("/ops/devices", headers={"X-Ops-Token": "test-token"})
        devices = r.json()
        d = next(x for x in devices if x["device_id"] == device_id)
        assert d["trusted"] is True
        assert d["online"] is True


def test_ota_upgrade_delivered_in_handshake(client: TestClient) -> None:
    """发布新版本 → 设备重连握手直接携带 upgrade 元数据（重连即检查）。"""
    import io

    device_id = "dev-ota-1"
    files = {"file": ("ntu-shower-0.3.0.apk", io.BytesIO(b"PKx34fake"), "application/octet-stream")}
    client.post(
        "/ops/releases",
        headers={"X-Ops-Token": "test-token"},
        data={"version": "0.3.0", "version_code": 6, "mandatory": "false", "force": "false", "target": device_id},
        files=files,
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = _recv_until(ws, "link.hello_ack")
        upgrade = ack["payload"]["upgrade"]
        assert upgrade is not None
        assert upgrade["version"] == "0.3.0"
        assert len(upgrade["sha256"]) == 64


def test_force_release_not_repushed_when_device_already_on_that_version(client: TestClient) -> None:
    """回归：设备已在目标版本时，force 发布不得再次下发（防无限重装循环）。

    事故场景（2026-09-30 真机复现）：force 发布 + release_install 无记录时，
    每次 hello 都携带 upgrade → 设备下载→安装→重启→重连→再下载，约 7 秒一轮。
    根因是 mark_installed_upto 定义了却从未被调用；本用例锁定调用点存在且
    早于 latest_for 执行。
    """
    import io

    from app.modules.ota import repository as ota_repository

    device_id = "dev-loop-guard"
    files = {"file": ("ntu-shower-0.3.1.apk", io.BytesIO(b"PKx34fake"), "application/octet-stream")}
    client.post(
        "/ops/releases",
        headers={"X-Ops-Token": "test-token"},
        data={"version": "0.3.1", "version_code": 8, "mandatory": "false", "force": "true", "target": device_id},
        files=files,
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id, version_code=8))
        ack = _recv_until(ws, "link.hello_ack")
        assert ack["payload"]["upgrade"] is None, "设备已在该版本，force 发布不得再推送"

    assert ota_repository.is_installed(device_id, "0.3.1") is True


def test_unexpected_frame_error_is_logged_before_reraise(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """帧处理里的未预期异常必须记录后重新抛出。

    静默吞掉会让「设备莫名掉线」无从追查——日志里什么都没有，只能靠猜。而
    `WebSocketDisconnect` 是常态（移动网络、重启），不该按异常处理，所以两类
    要分开：这个用例锁的是「其他异常必须有痕迹」。
    """
    from app.gateway import routes as gateway_routes

    # 消息走变量：ruff 的 EM101 禁止异常里直接用字符串字面量
    boom_message = "frame handler exploded"

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(boom_message)

    monkeypatch.setattr(gateway_routes, "_handle_frame", boom)

    device_id = "dev-gw-boom"
    # 异常会穿透到 TestClient，这里只关心它有没有被记下来。
    #
    # 读到连接中断为止，而不是只读一帧：`link.hello` 之后服务端会回 hello_ack 并主动发
    # 一次探测 ping，它们都排在触发异常的那一帧之前。只读一帧时异常尚未发生，断言会假失败。
    with (
        caplog.at_level(logging.WARNING, logger="ntu-shower.gateway"),
        contextlib.suppress(Exception),
        client.websocket_connect("/gateway/device") as ws,
    ):
        ws.send_text(_hello(device_id))
        ws.send_text(_envelope("link.ping", {"sender_time": "2026-09-29T12:00:01.000Z"}))
        while True:
            ws.receive_text()

    assert any(getattr(record, "event", "") == "gateway.session-failed" for record in caplog.records), (
        "未预期的帧处理异常没有留痕"
    )


def test_device_update_payload_is_the_whole_record(fresh_db: str) -> None:
    """上下线推的是**完整档案**，不是一个状态词。

    这条锁的是"前端拿得到版本号、来源 IP、开机时刻"：设备重连时这几个字段在服务端刚被
    更新过（OTA 升级换了版本号、换网络换了来源 IP、重启换了开机时刻），而 payload 若只剩
    `device_id`，前端收到也没有别的字段可更新——那些变化就永远到不了界面。此前正是那样：
    `device_online` 的 payload 只有 id 与 trusted。
    """
    import asyncio

    from app.gateway import routes as gateway_routes
    from app.modules.devices import repository as devices_repository

    device_id = "dev-update-1"
    devices_repository.register(
        device_id, "0.3.33", 40, "4.4.4", ["session.assign"], "10.0.0.9", "2026-06-01T08:00:00.000Z"
    )

    record = asyncio.run(gateway_routes._device_snapshot(device_id))

    assert record["device_id"] == device_id
    assert record["app_version"] == "0.3.33"
    assert record["version_code"] == 40
    assert record["os_version"] == "4.4.4"
    assert record["capabilities"] == ["session.assign"]
    assert record["last_ip"] == "10.0.0.9"
    assert record["booted_at"] == "2026-06-01T08:00:00.000Z"


def test_device_update_with_missing_record_keeps_id_and_offline(fresh_db: str) -> None:
    """设备记录查不到时，推一个只含 id 与"不在线"的最小对象，而不是让推送失败。

    设备刚被删掉的那一瞬确实可能查不到，而订阅者（网页端）该收到一条能落地处理的
    消息，而不是什么都收不到。
    """
    import asyncio

    from app.gateway import routes as gateway_routes

    record = asyncio.run(gateway_routes._device_snapshot("dev-never-registered"))

    assert record == {"device_id": "dev-never-registered", "online": False}


def test_event_type_names_use_dots() -> None:
    """事件名用点号，与设备协议（`link.hello`、`door.snapshot`）一致。

    此前这里用下划线（`device_online`、`door_update`），同一套系统两种分隔符。这条防止
    有人顺手改回去。
    """
    from app.core import events

    assert events.DEVICE_UPDATE == "device.update"
    assert events.DOOR_UPDATE == "door.update"


def test_online_reflects_connection_not_storage(fresh_db: str) -> None:
    """档案在库里、连接不在内存里 → 报离线。

    这正是进程刚重启后的状态：连接表空了，而档案还在。此前档案表另存一个在线位，
    于是重启后前端一直显示"在线"——一台根本没有连接的设备。每次部署都会进入这个状态，
    而它看起来又"正常"，因为设备很快会重连上来把自己再次置为在线。
    """
    from typing import cast

    from fastapi import WebSocket

    from app.gateway.manager import manager
    from app.modules.devices import repository, service

    device_id = "dev-online-src"
    repository.register(device_id, "0.3.35", 42, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")

    record = service.get_device(device_id)
    assert record is not None, "档案该在库里"
    assert record["online"] is False, "库里没有连接，就不能报在线"
    listed = next(item for item in service.list_devices() if item["device_id"] == device_id)
    assert listed["online"] is False

    manager.register(device_id, cast(WebSocket, object()))
    assert service.get_device(device_id)["online"] is True
    manager.unregister(device_id, cast(WebSocket, object()))  # 不是同一条，按设计不动
    assert service.get_device(device_id)["online"] is True, "摘掉别的连接不该影响这一条"


def test_heartbeat_refreshes_last_seen(client: TestClient) -> None:
    """心跳刷新 last_seen_at，让这个字段名副其实。

    此前只有上线与下线两处写它，于是设备跑了几小时之后，那一列还停在连接建立那一刻，
    而前端那行写的是"最后上报"。

    验证方式是把那一列先改成显然过期的值再发心跳——直接比较前后两次 `now_rfc3339()`
    不行，测试里 register 与心跳之间只隔一次进程内 WS 往返，可能落在同一毫秒。
    """
    from app.core.db import get_conn
    from app.modules.devices import service

    device_id = "dev-heartbeat"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")

        conn = get_conn()
        conn.execute(
            "UPDATE device SET last_seen_at = ? WHERE device_id = ?",
            ("2020-01-01T00:00:00.000Z", device_id),
        )
        conn.commit()

        ws.send_text(_envelope("link.ping", {"sender_time": "2026-09-29T12:00:01.000Z"}))
        _recv_until(ws, "link.pong")

        record = service.get_device(device_id)
        assert record is not None
        assert record["last_seen_at"] > "2020-01-01T00:00:00.000Z", (
            f"心跳该把 last_seen_at 往前推，实际是 {record['last_seen_at']!r}"
        )
