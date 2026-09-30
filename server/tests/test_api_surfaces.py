"""API 面覆盖测试：只读接口、影子、配置下发、OTA、用户管理、命令审计。"""

import json
import uuid
from collections.abc import Coroutine, Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.core import actions, events


def _hello(device_id: str) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.2.4",
                "version_code": 6,
                "os_version": "4.4.4",
                "capabilities": [],
            },
        }
    )


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


def _ops_token(client: TestClient) -> dict:
    return {"X-Ops-Token": "test-token"}


@contextmanager
def _online_device(client: TestClient, device_id: str) -> Iterator[WebSocketTestSession]:
    """建档 → 信任 → 重连，产出一个保持在线会话。

    必须是上下文管理器：手动 __enter__/__exit__ 一旦漏掉退出（断言失败时最容易
    发生），连接就泄漏并让 TestClient 的 portal 挂起，症状是整个测试会话卡死。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
    client.patch(f"/ops/devices/{device_id}", headers=_ops_token(client), json={"trusted": True})
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
        yield ws


# ── 用户面只读接口 ──


def test_list_doors_empty(client: TestClient) -> None:
    assert client.get("/api/v1/doors").json() == []


def test_list_doors_filtered_by_device(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository
    from app.modules.doors import service as doors_service

    devices_repository.register("dev-doors-1", "0.2.4", 6, "4.4.4", [], "127.0.0.1")
    doors_service.process_snapshot(
        "dev-doors-1",
        {
            "snapshot_id": str(uuid.uuid4()),
            "source": "db_watch",
            "doors": [
                {
                    "door_no": 2,
                    "card_no": 0,
                    "cert_no": "",
                    "acc_no": "",
                    "open_time": None,
                    "in_time": None,
                    "close_time": None,
                    "bath_time_s": 0,
                    "status_raw": 0,
                }
            ],
        },
    )
    doors = client.get("/api/v1/doors", params={"device_id": "dev-doors-1"}).json()
    assert len(doors) == 1
    assert doors[0]["door_no"] == 2
    assert doors[0]["state"] == "free"
    assert doors[0]["device_name"] == ""


def test_stream_pushes_door_update(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream") as ws:
        events.publish("door_update", {"device_id": "d", "doors": [], "at": "t"})
        assert json.loads(ws.receive_text())["type"] == "door_update"


# ── 影子 ──


def test_shadow_user_and_ops_views(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-shadow-1", "0.2.4", 6, "4.4.4", ["session.assign"], "127.0.0.1")
    user_view = client.get("/api/v1/devices/dev-shadow-1/shadow")
    assert user_view.status_code == 200
    # 用户面直接返回 reported，不再多包一层 device_id（路径里已有，device 里也有）
    reported = user_view.json()
    assert set(reported) == {"device", "doors"}
    assert reported["device"]["app_version"] == "0.2.4"
    assert reported["device"]["capabilities"] == ["session.assign"]

    ops_view = client.get("/ops/devices/dev-shadow-1/shadow", headers=_ops_token(client))
    assert ops_view.status_code == 200
    doc = ops_view.json()
    assert set(doc) == {"reported", "desired", "metadata"}
    # 两处的 reported 必须是同一形状，否则客户端要为它们各写一套解析
    assert doc["reported"] == reported
    assert doc["desired"]["config"]["heartbeat_interval_s"] == 30

    assert client.get("/api/v1/devices/dev-none/shadow").status_code == 404
    assert client.get("/ops/devices/dev-none/shadow", headers=_ops_token(client)).status_code == 404


# ── 远程配置（管理面）──


def test_global_config_get_put(client: TestClient) -> None:
    r = client.get("/ops/config/global", headers=_ops_token(client))
    assert r.status_code == 200
    assert r.json()["revision"] == 1

    r = client.put("/ops/config/global", headers=_ops_token(client), json={"config": {"heartbeat_interval_s": 45}})
    assert r.json()["revision"] == 2

    global_cfg = client.get("/ops/config/global", headers=_ops_token(client)).json()
    assert global_cfg["config"]["heartbeat_interval_s"] == 45
    # 未覆盖的键仍是默认值：只存差异才能让默认值保持可演进
    assert global_cfg["config"]["ota_check_interval_s"] == 3600


def test_unknown_config_key_is_rejected(client: TestClient) -> None:
    """未知键此前被静默丢弃——调用方以为提交成功，实际什么都没发生。"""
    r = client.put("/ops/config/global", headers=_ops_token(client), json={"config": {"nonexistent_key": 1}})

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/invalid-config")


def test_out_of_range_config_is_rejected(client: TestClient) -> None:
    """0 会让设备立刻重发心跳，形成风暴，因此在入口拦下。"""
    r = client.put("/ops/config/global", headers=_ops_token(client), json={"config": {"heartbeat_interval_s": 0}})

    assert r.status_code == 422


def test_config_spec_endpoint_lists_every_key(client: TestClient) -> None:
    """客户端据此渲染控件，不必各自硬编码键表。"""
    spec = client.get("/ops/config/spec", headers=_ops_token(client)).json()

    assert {item["key"] for item in spec} >= {"heartbeat_interval_s", "overlay_enabled", "auto_settle_threshold_s"}
    heartbeat = next(item for item in spec if item["key"] == "heartbeat_interval_s")
    assert heartbeat["kind"] == "int"
    assert heartbeat["scope"] == "device"
    assert heartbeat["minimum"] == 5
    assert heartbeat["unit"] == "秒"


def test_device_config_override_lifecycle(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备在线但不应答（无 ack）时：推送已送达、覆盖照常生效。"""
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)
    device_id = "dev-cfg-1"
    with _online_device(client, device_id):
        r = client.put(
            f"/ops/devices/{device_id}/config",
            headers=_ops_token(client),
            json={"config": {"heartbeat_interval_s": 20}},
        )
        body = r.json()
        # 推送已送达但设备未确认：notified 为真、applied 为假并带原因
        assert {k: body[k] for k in ("device_id", "revision", "notified", "applied", "reason")} == {
            "device_id": device_id,
            "revision": 1,
            "notified": True,
            "applied": False,
            "reason": "ack timeout",
        }
        assert body["config"]["heartbeat_interval_s"] == 20

        override = client.get(f"/ops/devices/{device_id}/config", headers=_ops_token(client)).json()
        assert override["config"]["heartbeat_interval_s"] == 20
        assert override["overrides"] == {"heartbeat_interval_s": 20}

        removed = client.delete(f"/ops/devices/{device_id}/config", headers=_ops_token(client))
        assert removed.json()["override_removed"] is True
        after = client.get(f"/ops/devices/{device_id}/config", headers=_ops_token(client)).json()
        assert after["overrides"] is None
        assert after["config"]["heartbeat_interval_s"] == 30


def test_device_config_rejected_when_offline(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-cfg-off", "0.2.4", 6, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted("dev-cfg-off", True)
    r = client.put("/ops/devices/dev-cfg-off/config", headers=_ops_token(client), json={"config": {}})
    assert r.status_code == 409


def test_same_channel_queues_second_command(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """同通道的第二条命令**排队等待**，不拒绝。

    这个页面是给一个人调试用的，他的操作天生有先后意图——点截图、再点读树，两个都要。
    丢弃第二条等于让"刷新"永远只能成功一半。设备端的 `SerialCommandQueue` 本来就是排队、
    从不丢弃，服务端不该比它更严。
    """
    import asyncio

    from app.core import config as config_module
    from app.gateway import manager as manager_module
    from app.modules.sessions import service as sessions_service

    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)

    class FakeWS:
        async def send_text(self, data: str) -> bool:
            return True

    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-busy": FakeWS()})

    async def flow() -> None:
        first = asyncio.create_task(
            sessions_service.send_command_and_wait("dev-busy", actions.CAMERA_CAPTURE, {}, timeout_s=0.3)
        )
        await asyncio.sleep(0.05)
        assert sessions_service.is_device_busy("dev-busy", actions.CHANNEL_CAPTURE)

        # 第二条在通道上排队：等第一条超时后自己才执行，然后自己也超时。
        # 状态是 timeout 而不是 rejected——它被执行过，只是没人应答。
        second = await sessions_service.send_command_and_wait("dev-busy", actions.CAMERA_CAPTURE, {}, timeout_s=0.3)
        assert second["status"] == "timeout"
        assert (await first)["status"] == "timeout"
        assert not sessions_service.is_device_busy("dev-busy", actions.CHANNEL_CAPTURE)

    asyncio.run(flow())

    # 审计表里不该出现 rejected：这两条都被执行过
    rows = client.get("/ops/commands", headers=_ops_token(client), params={"device_id": "dev-busy"}).json()
    assert not any(r["status"] == "rejected" for r in rows)


def test_server_overloaded_rejects(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """排队满了才拒绝——那是过载信号，与"通道正忙"不是一回事。

    上限必须存在：队列无限增长时，排在后面的早就超过客户端超时了，让它继续等没有意义。
    """
    import asyncio

    from app.gateway import manager as manager_module
    from app.modules.sessions import service as sessions_service

    class FakeWS:
        async def send_text(self, data: str) -> bool:
            return True

    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-full": FakeWS()})

    async def flow() -> None:
        # 占满队列：队首在执行、其余在等
        running = [
            asyncio.create_task(
                sessions_service.send_command_and_wait("dev-full", actions.CAMERA_CAPTURE, {}, timeout_s=0.5)
            )
            for _ in range(sessions_service.CHANNEL_QUEUE_MAX)
        ]
        await asyncio.sleep(0.05)

        # 再来一条：队满，立刻拒绝而不是无限等
        overflow = await sessions_service.send_command_and_wait("dev-full", actions.CAMERA_CAPTURE, {}, timeout_s=0.5)
        assert overflow["status"] == "rejected"
        assert overflow["reason_code"] == "server.overloaded"

        for task in running:
            await task

    asyncio.run(flow())


def test_channels_do_not_block_each_other(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """不同通道互不阻塞。

    设备端把命令分到几条互不阻塞的路径上跑（自动化 / 采集 / 维护），服务端按同一划分
    排队。若按"每台设备一条"处理，设备明明能同时做的事会被排成一队——真机上出现过前端
    按 3 秒轮询截图，把人工发的 `device.exec` 堵在后面。
    """
    import asyncio

    from app.gateway import manager as manager_module
    from app.modules.sessions import service as sessions_service

    class FakeWS:
        async def send_text(self, data: str) -> bool:
            return True

    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-lane": FakeWS()})

    async def flow() -> None:
        # 采集通道占住一条
        photo = asyncio.create_task(
            sessions_service.send_command_and_wait("dev-lane", actions.CAMERA_CAPTURE, {}, timeout_s=0.3)
        )
        await asyncio.sleep(0.05)
        assert sessions_service.is_device_busy("dev-lane", actions.CHANNEL_CAPTURE)

        # 维护通道不受影响：并行下发，各自等各自的超时
        exec_result = await sessions_service.send_command_and_wait(
            "dev-lane",
            actions.DEVICE_EXEC,
            {"command": "echo hi"},
            timeout_s=0.3,
        )
        assert exec_result["status"] == "timeout"
        assert exec_result["reason_code"] == "command.deadline_exceeded"

        assert (await photo)["status"] == "timeout"

    asyncio.run(flow())


def test_cancel_while_sending_releases_device(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """下发阶段被取消，设备必须立刻解除 busy，审计也要收尾。

    登记在途命令发生在下发之前，而下发是 await 点：调用方在这期间断开连接会取消
    整个协程。清理若只罩住"等结果"那一段，设备会被永久标成 busy 直到服务重启——
    真机上发生过，`command_audit` 里留下一行 `finished_at` 为空的记录。
    """
    import asyncio

    from app.gateway import manager as manager_module
    from app.modules.sessions import service as sessions_service

    async def hanging_send(device_id: str, envelope: dict[str, object]) -> bool:
        await asyncio.sleep(3600)
        return True

    monkeypatch.setattr(manager_module.manager, "send_to", hanging_send)

    async def flow() -> None:
        task = asyncio.create_task(
            sessions_service.send_command_and_wait("dev-cancel", actions.DEVICE_EXEC, {}, timeout_s=1)
        )
        await asyncio.sleep(0.05)
        assert sessions_service.is_device_busy("dev-cancel", actions.CHANNEL_MAINTENANCE)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not sessions_service.is_device_busy("dev-cancel", actions.CHANNEL_MAINTENANCE), "取消后设备仍被标记为忙"

    asyncio.run(flow())

    rows = client.get("/ops/commands", headers=_ops_token(client), params={"device_id": "dev-cancel"}).json()
    assert len(rows) == 1
    assert rows[0]["finished_at"] is not None, "取消的请求留下了永不收尾的审计记录"
    assert rows[0]["reason_code"] == "command.aborted"


# ── 用户管理（管理面）──


# ── OTA（设备面与管理面）──


def test_ota_manifest_and_download_flow(client: TestClient) -> None:
    assert client.get("/ota/manifest", params={"device_id": "dev-ota-1", "version_code": 6}).json() == {
        "up_to_date": True
    }

    # 上传 → 常规发布对 version_code=6 不适用
    from app.modules.ota import service as ota_service

    ota_service.store_release("0.2.5", b"apk-bytes")
    from app.modules.ota import repository as ota_repository

    ota_repository.insert_release("0.2.5", 7, "a" * 64, False, False, "dev-ota-1")
    manifest = client.get("/ota/manifest", params={"device_id": "dev-ota-1", "version_code": 6}).json()
    assert manifest["up_to_date"] is False
    assert manifest["version"] == "0.2.5"

    assert client.get("/ota/download/0.2.5").content == b"apk-bytes"
    assert client.get("/ota/download/9.9.9").status_code == 404  # 格式合法但未发布
    assert client.get("/ota/download/no-such").status_code == 422  # 格式非法，HTTP 边界拦截

    releases = client.get("/ota/releases").json()
    assert any(r["version"] == "0.2.5" for r in releases)


def test_ota_upload_rejects_oversize(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "max_ota_bytes", 8)
    r = client.post(
        "/ops/releases",
        headers=_ops_token(client),
        data={"version": "9.9.9", "version_code": 99},
        files={"file": ("big.apk", b"x" * 9)},
    )
    assert r.status_code == 413


# ── 命令审计（管理面）──


def test_command_audit_listing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import manager as manager_module
    from app.modules.sessions import service as sessions_service

    class FakeWS:
        async def send_text(self, data: str) -> bool:
            return True

    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-audit": FakeWS()})
    result = asyncio_run_command(
        sessions_service.send_command_and_wait("dev-audit", actions.CAMERA_CAPTURE, {}, timeout_s=1)
    )
    assert result["status"] == "timeout"

    rows = client.get("/ops/commands", headers=_ops_token(client), params={"device_id": "dev-audit"}).json()
    assert rows
    assert rows[0]["status"] == "timeout"
    all_rows = client.get("/ops/commands", headers=_ops_token(client), params={"limit": 1}).json()
    assert len(all_rows) == 1


def asyncio_run_command(coroutine: Coroutine[object, object, dict[str, object]]) -> dict:
    import asyncio

    return asyncio.run(coroutine)


# ── 设备时钟与时间边界 ──


def test_device_clock_boundaries() -> None:
    from app.core import device_clock

    assert device_clock.update_skew("dev-clock", "not-a-time") == 0.0
    assert device_clock.correct_rfc3339(None, "dev-clock") is None
    assert device_clock.correct_rfc3339("bad", "dev-clock") == "bad"
    corrected = device_clock.correct_rfc3339("2026-09-30T12:00:00.000Z", "dev-clock")
    assert corrected.endswith("Z")


def test_ping_payload_uses_symmetric_sender_time() -> None:
    from app.core import device_clock, protocol

    frame = json.loads(device_clock.ping_payload())
    assert frame["type"] == "link.ping"
    assert "sender_time" in frame["payload"]
    assert frame["v"] == protocol.PROTOCOL_VERSION
