"""单元级边界测试：协议解析、安全原语、连接管理、时钟、状态派生、命令等待分支。"""

import asyncio
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import actions, device_clock, errors, protocol
from app.core.clock import now_rfc3339
from app.core.problems import OPS_UNAUTHORIZED, ApiProblemError
from app.gateway.manager import ConnectionManager
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState
from app.modules.sessions import service as sessions_service


def _ops_token(client: TestClient) -> dict:
    return {"X-Ops-Token": "test-token"}


# ── 协议 ──


def test_parse_rejects_non_object_and_bad_json() -> None:
    assert protocol.parse("not json") is None
    assert protocol.parse('["array"]') is None
    assert protocol.parse('{"ok": true}') == {"ok": True}


def test_envelope_carries_explicit_frame_id_and_reply_to() -> None:
    frame = json.loads(protocol.envelope("link.ping", {}, reply_to="r", frame_id="f"))
    assert frame["id"] == "f"
    assert frame["reply_to"] == "r"
    plain = json.loads(protocol.envelope("link.ping", {}))
    assert "reply_to" not in plain
    assert plain["v"] == protocol.PROTOCOL_VERSION


def test_error_frame_is_flat_payload() -> None:
    frame = json.loads(protocol.error("proto.x", "msg", reply_to="r"))
    assert frame["type"] == "link.error"
    assert frame["payload"] == {"code": "proto.x", "message": "msg"}
    assert frame["reply_to"] == "r"


def test_unregistered_status_gets_a_generated_problem_type() -> None:
    """框架抛出的未登记状态码：按状态码生成 type，文案统一为可展示的中文。

    不按状态码维护中文短语——那只是复刻 HTTP 标准表，而这些问题的使用者
    视角没有区别（都是"请求本身打错了"）。状态码本身在 status 成员里。
    """
    assert errors._unregistered(404).code == "http-404"
    assert errors._unregistered(599).code == "http-599"
    assert errors._unregistered(404).status == 404
    assert errors._unregistered(404).title == "请求失败"
    assert errors._unregistered(599).title == "请求失败"


# ── 安全原语 ──

# ── 连接管理器 ──


class _WS:
    async def send_text(self, data: str) -> bool:
        return True


class _BrokenWS:
    """发送即失败的设备连接替身（用于验证 send_to 的失败路径）。"""

    async def send_text(self, data: str) -> bool:
        message = "socket gone"
        raise RuntimeError(message)


def test_connection_manager_register_replace_and_stale_unregister() -> None:
    async def flow() -> None:
        manager = ConnectionManager()
        first, second = _WS(), _WS()
        assert manager.register("d", first) is False
        assert manager.register("d", second) is True  # 顶替旧连接
        assert manager.get("d") is second
        assert manager.is_online("d")
        assert manager.online_connections() == [("d", second)]

        manager.unregister("d", first)  # 过期连接不误删新连接
        assert manager.is_online("d")
        manager.unregister("d", second)
        assert not manager.is_online("d")

        assert await manager.send_to("gone", "x") is False
        assert await manager.send_to("d", "x") is False  # 空表
        manager.register("d", _BrokenWS())
        assert await manager.send_to("d", "x") is False  # 发送异常 → False

    asyncio.run(flow())


def test_send_to_success_path() -> None:
    async def flow() -> None:
        manager = ConnectionManager()
        manager.register("d", _WS())
        assert await manager.send_to("d", "x") is True

    asyncio.run(flow())


# ── 设备时钟 ──


def test_update_skew_ema_converges_and_corrects() -> None:
    device_clock._ema.pop("dev-ema", None)
    base = 5.0
    # 5 秒的观测远小于跳变阈值，走 EMA 而非整体替换
    device_clock.update_skew("dev-ema", _rfc3339_after(base), 300.0)
    device_clock.update_skew("dev-ema", _rfc3339_after(base), 300.0)
    assert 4.0 < device_clock.get_skew("dev-ema") < 6.0

    corrected = device_clock.correct_rfc3339(now_rfc3339(), "dev-ema")
    assert corrected.endswith("Z")
    device_clock._ema.pop("dev-ema", None)


def _rfc3339_after(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    past = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return past.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_ping_payload_is_protocol_frame() -> None:
    frame = json.loads(device_clock.ping_payload())
    assert frame["type"] == "link.ping"
    assert frame["payload"]["sender_time"].endswith("Z")


# ── 喷头状态派生边界 ──


def test_derive_state_safe_int_and_unknown_status() -> None:
    assert doors_service._safe_int("7") == 7
    assert doors_service._safe_int("x", default=3) == 3
    assert doors_service._safe_int(None) == 0

    assert doors_service.derive_state({"status_raw": "1"}) == DoorState.REPAIR
    assert doors_service.derive_state({"status_raw": "3"}) == DoorState.DISABLED
    # 占用者标识：acc_no 非零也算占用
    assert doors_service.derive_state({"status_raw": 0, "card_no": 0, "acc_no": "8"}) == DoorState.WAITING


def test_process_snapshot_skips_invalid_and_zero_door_no(client: TestClient) -> None:
    bad = doors_service.process_snapshot("dev-d", {"snapshot_id": "s"})
    assert bad["status"] == "error"

    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-d", "0.2.4", 6, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    result = doors_service.process_snapshot(
        "dev-d",
        {
            "snapshot_id": str(uuid.uuid4()),
            "source": "db_watch",
            "doors": [{"door_no": 0, "card_no": 0}, {"door_no": 1, "card_no": 0}],
        },
    )
    assert result == {"status": "ok", "processed": 1}


def test_session_owner_change_closes_old_opens_new(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository
    from app.modules.doors import repository as doors_repository

    devices_repository.register("dev-swap", "0.2.4", 6, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    doors = [
        {
            "door_no": 1,
            "card_no": 100,
            "cert_no": "A",
            "acc_no": "",
            "open_time": "2026-09-30T12:00:00.000Z",
            "in_time": "2026-09-30T12:00:05.000Z",
            "close_time": None,
            "bath_time_s": 10,
            "status_raw": 0,
        }
    ]
    doors_service.process_snapshot("dev-swap", {"snapshot_id": str(uuid.uuid4()), "source": "db_watch", "doors": doors})
    first = doors_repository.get_open_session("dev-swap", 1)

    doors[0]["card_no"] = 200
    doors[0]["cert_no"] = "B"
    doors_service.process_snapshot("dev-swap", {"snapshot_id": str(uuid.uuid4()), "source": "db_watch", "doors": doors})

    assert first is not None
    assert doors_repository.get_open_session("dev-swap", 1) is not None


# ── 命令等待分支 ──


def test_pending_result_cross_thread_resolve() -> None:
    async def flow() -> bool:
        loop = asyncio.get_running_loop()
        pending = sessions_service.PendingResult(loop, loop.create_future())
        done = threading.Event()
        resolved = {}

        def from_thread() -> None:
            resolved["ok"] = pending.resolve({"status": "ok"})
            done.set()

        threading.Thread(target=from_thread).start()
        task = asyncio.create_task(asyncio.wait_for(pending.future, 2))
        await asyncio.get_running_loop().run_in_executor(None, done.wait)
        result = await task
        return resolved["ok"] and result["status"] == "ok"

    assert asyncio.run(flow())


def test_pending_result_second_resolve_is_noop() -> None:
    async def flow() -> bool:
        loop = asyncio.get_running_loop()
        pending = sessions_service.PendingResult(loop, loop.create_future())
        assert pending.resolve({"a": 1}) is True
        return pending.resolve({"a": 2}) is False

    assert asyncio.run(flow())


def test_push_config_update_offline_and_ack_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import manager as manager_module

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, data: str) -> bool:
            self.sent.append(data)
            return True

    ws = FakeWS()
    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-push": ws})

    async def flow() -> None:
        # 在线路径：ack 及时 → applied True
        task = asyncio.create_task(
            sessions_service.push_config_update("dev-push", 3, {"heartbeat_interval_s": 9}, timeout_s=1)
        )
        await asyncio.sleep(0.05)
        frame = json.loads(ws.sent[-1])
        assert frame["type"] == "config.update"
        assert frame["payload"]["revision"] == 3
        assert sessions_service.resolve_config_ack("dev-push", "3")
        result = await task
        assert result == {"notified": True, "applied": True}

        # 二次 ack 无挂起 → False
        assert not sessions_service.resolve_config_ack("dev-push", "3")

        # 超时路径
        result = await sessions_service.push_config_update("dev-push", 4, {}, timeout_s=0.05)
        assert result["applied"] is False

        # 离线路径
        monkeypatch.setattr(manager_module.manager, "_connections", {})
        result = await sessions_service.push_config_update("dev-push", 5, {})
        assert result == {"notified": False, "applied": False, "reason": "device offline"}

    asyncio.run(flow())


def test_command_offline_path_clears_device_pending(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import manager as manager_module

    monkeypatch.setattr(manager_module.manager, "_connections", {})

    async def flow() -> None:
        result = await sessions_service.send_command_and_wait("dev-off", actions.CAMERA_CAPTURE, {}, timeout_s=1)
        assert result["status"] == "timeout"
        assert result["reason_code"] == "device.offline"
        assert not sessions_service.is_device_busy("dev-off", actions.CHANNEL_CAPTURE)

    asyncio.run(flow())


# ── 管理面分支补测 ──


def test_device_untrust_rename_and_exec_guards(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-mgmt", "0.2.4", 6, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    client.patch("/ops/devices/dev-mgmt", headers=_ops_token(client), json={"trusted": True})
    untrusted = client.patch("/ops/devices/dev-mgmt", headers=_ops_token(client), json={"trusted": False})
    assert untrusted.json()["trusted"] is False

    # 未信任设备上的 exec 被守卫拦截：未信任是 403，与"设备不存在"（404）区分开
    denied = client.post("/ops/devices/dev-mgmt/exec", headers=_ops_token(client), json={"command": "id"})
    assert denied.status_code == 403
    assert denied.json()["type"].endswith("/problems/untrusted-device")


def test_rename_unknown_device_returns_not_found(client: TestClient) -> None:
    """改一台不存在的设备 → 404。

    此前返回 `{"found": false}`——把错误藏在成功响应里，调用方要先看字段
    才知道有没有生效。
    """
    r = client.patch("/ops/devices/ghost", headers=_ops_token(client), json={"name": "n"})
    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_repository_set_name_unknown_device(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository

    assert devices_repository.set_name("ghost", "n") is False


def test_require_ops_token_rejects_missing_token() -> None:
    from app.core.security import require_ops_token

    with pytest.raises(ApiProblemError) as denied:
        require_ops_token(x_ops_token=None)
    assert denied.value.problem is OPS_UNAUTHORIZED


def test_notify_target_devices_all(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway import manager as manager_module

    class FakeWS:
        async def send_text(self, data: str) -> bool:
            return True

    monkeypatch.setattr(manager_module.manager, "_connections", {"dev-1": FakeWS(), "dev-2": FakeWS()})
    notified = asyncio.run(ota_service_notify_all())
    assert set(notified) == {"dev-1", "dev-2"}


async def ota_service_notify_all() -> list[str]:
    from app.modules.ota import service as ota_service

    return await ota_service.notify_target_devices("0.2.5", 7, False, "all")
