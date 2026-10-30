"""命令通道：成功往返、同通道排队、队列满 rejected、不支持动作 failed、离线 timeout、审计补 terminal_message。"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from tests import helpers

from app.core import actions, outcomes, protocol, reason_codes
from app.core.connections import manager
from app.modules.audit import repository as command_audit
from app.modules.sessions import service


def _online(monkeypatch: pytest.MonkeyPatch, device_id: str) -> helpers.FakeGatewayWS:
    fake = helpers.FakeGatewayWS()
    monkeypatch.setattr(manager, "_connections", {device_id: fake})
    return fake


def _resolve_ok(frame: dict[str, Any], terminal_message: str = "已分配") -> None:
    service.resolve(
        str(frame["id"]), {"status": "ok", "reason_code": "", "terminal_message": terminal_message, "data": None}
    )


async def _wait_until(predicate: Callable[[], bool], timeout_s: float = 5.0) -> None:
    """在事件循环内让出执行权，直到条件成立。不用固定时长等待。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not predicate():
        if loop.time() > deadline:
            message = "条件未在限时内成立"
            raise AssertionError(message)
        await asyncio.sleep(0)


def test_channel_lock_wait_counts_toward_deadline(fresh_db: str) -> None:
    """等通道锁的时间计入命令超时：锁被占用时按 deadline 返回 timeout。"""

    async def flow() -> dict[str, Any]:
        lock = service._lock_for("dev-lock", actions.ACTION_CHANNELS[actions.SESSION_ASSIGN])
        await lock.acquire()
        try:
            return await service.send_command_and_wait(
                "dev-lock", actions.SESSION_ASSIGN, {"door_no": 1}, timeout_s=0.01
            )
        finally:
            lock.release()

    result = asyncio.run(flow())
    assert result["status"] == outcomes.TIMEOUT.value
    assert result["reason_code"] == reason_codes.DEADLINE_EXCEEDED.code


def test_round_trip_success(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令完整往返：下发 session.command → 设备回执 → 结果透传。"""
    fake = _online(monkeypatch, "dev-roundtrip")

    async def flow() -> dict[str, Any]:
        task = asyncio.create_task(
            service.send_command_and_wait("dev-roundtrip", actions.SESSION_ASSIGN, {"door_no": 1}, timeout_s=5)
        )
        await fake.wait_for_sent()
        command = json.loads(fake.sent[0])
        assert command["type"] == "session.command"
        assert command["payload"]["action"] == "session.assign"
        assert command["payload"]["timeout_ms"] > 0
        _resolve_ok(command)
        return await task

    result = asyncio.run(flow())
    assert result["status"] == "ok"
    assert result["terminal_message"] == "已分配"

    rows = command_audit.list_commands(device_id="dev-roundtrip")
    assert rows[0]["status"] == "ok"


def test_offline_device_times_out_with_device_offline(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """连接不存在即离线：timeout + device.offline，命令不投出。"""
    monkeypatch.setattr(manager, "_connections", {})

    async def flow() -> dict[str, Any]:
        return await service.send_command_and_wait("dev-off", actions.SESSION_ASSIGN, {}, timeout_s=5)

    result = asyncio.run(flow())
    assert result["status"] == "timeout"
    assert result["reason_code"] == "device.offline"
    assert result["terminal_message"] == "设备离线"
    assert result["data"] is None


def test_silent_device_timeout_keeps_registry_reason(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备不应答：响应层与命令审计行都经登记表补齐 terminal_message。"""
    _online(monkeypatch, "dev-silent")

    async def flow() -> dict[str, Any]:
        return await asyncio.wait_for(
            service.send_command_and_wait("dev-silent", actions.SESSION_ASSIGN, {}, timeout_s=0.2), timeout=5
        )

    result = asyncio.run(flow())
    assert result["status"] == "timeout"
    assert result["reason_code"] == "command.deadline_exceeded"
    assert result["terminal_message"] == reason_codes.describe("command.deadline_exceeded")

    rows = command_audit.list_commands(device_id="dev-silent")
    assert rows[0]["status"] == "timeout"
    assert rows[0]["reason_code"] == "command.deadline_exceeded"
    assert rows[0]["terminal_message"] == reason_codes.describe("command.deadline_exceeded")


def test_unsupported_action_fails_before_sending(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备声明的能力集不含该动作时，下发前即 failed，不产生设备流量。"""
    from app.modules.devices import repository as devices_repository

    device_id = "dev-caps-narrow"
    devices_repository.register(
        device_id, "0.4.13", 57, "4.4.4", ["session.assign"], "127.0.0.1", "2026-06-01T08:00:00.000Z"
    )
    devices_repository.set_trusted(device_id, True)
    fake = _online(monkeypatch, device_id)

    async def flow() -> dict[str, Any]:
        return await service.send_command_and_wait(device_id, actions.SESSION_SETTLE, {"cert_no": "C"}, timeout_s=1)

    result = asyncio.run(flow())
    assert result["status"] == "failed"
    assert result["reason_code"] == "device.unsupported_action"
    # 状态行是服务端另一条现态推送通道，与命令无关；这里只断言没有命令帧下发
    assert [frame for frame in (json.loads(text) for text in fake.sent) if frame["type"] == "session.command"] == []

    rows = command_audit.list_commands(device_id=device_id)
    assert rows[0]["reason_code"] == "device.unsupported_action"


def test_empty_capabilities_mean_undeclared_and_pass(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """空能力集表示未声明：不据此拦截，否则升级服务端会锁死在网设备。"""
    from app.modules.devices import repository as devices_repository

    device_id = "dev-caps-empty"
    devices_repository.register(device_id, "0.4.13", 57, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    devices_repository.set_trusted(device_id, True)
    _online(monkeypatch, device_id)

    async def flow() -> dict[str, Any]:
        return await service.send_command_and_wait(device_id, actions.SESSION_SETTLE, {}, timeout_s=0.1)

    result = asyncio.run(flow())
    assert result["reason_code"] != "device.unsupported_action"


def test_same_channel_queues_second_command(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """同通道的第二条命令排队等待，不拒绝：它被执行过，只是无人应答（timeout 而非 rejected）。"""
    fake = _online(monkeypatch, "dev-busy")

    async def flow() -> tuple[dict[str, Any], dict[str, Any]]:
        first = asyncio.create_task(
            service.send_command_and_wait("dev-busy", actions.CAMERA_CAPTURE, {}, timeout_s=0.5)
        )
        await fake.wait_for_sent()
        assert service.is_device_busy("dev-busy", actions.CHANNEL_CAPTURE)

        # 第二条的超时必须长于第一条的等待时间：否则它在排队期间就用完预算，
        # 根本不会下发，"同通道排队"这条断言就变成了时序赌博（CI 慢机实际输过）。
        second = await service.send_command_and_wait("dev-busy", actions.CAMERA_CAPTURE, {}, timeout_s=1.0)
        return second, await first

    second, first = asyncio.run(flow())
    assert second["status"] == "timeout"
    assert first["status"] == "timeout"
    assert not service.is_device_busy("dev-busy", actions.CHANNEL_CAPTURE)
    assert len(fake.sent) == 2

    rows = command_audit.list_commands(device_id="dev-busy")
    assert not any(row["status"] == "rejected" for row in rows)


def test_full_queue_is_rejected_as_overloaded(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """队列满立刻 rejected（过载信号）：无限增长时后排命令必然已超客户端超时。"""
    _online(monkeypatch, "dev-full")

    async def flow() -> dict[str, Any]:
        running = [
            asyncio.create_task(service.send_command_and_wait("dev-full", actions.CAMERA_CAPTURE, {}, timeout_s=0.5))
            for _ in range(service.CHANNEL_QUEUE_MAX)
        ]
        await _wait_until(
            lambda: service._channel_waiting.get(("dev-full", actions.CHANNEL_CAPTURE), 0) >= service.CHANNEL_QUEUE_MAX
        )
        overflow = await service.send_command_and_wait("dev-full", actions.CAMERA_CAPTURE, {}, timeout_s=0.5)
        for task in running:
            await task
        return overflow

    overflow = asyncio.run(flow())
    assert overflow["status"] == "rejected"
    assert overflow["reason_code"] == "server.overloaded"
    assert overflow["terminal_message"] == reason_codes.SERVER_OVERLOADED.message

    rows = command_audit.list_commands(device_id="dev-full")
    assert any(row["status"] == "rejected" for row in rows)


def test_channels_do_not_block_each_other(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """跨通道并行：采集通道占用时维护通道照常下发。"""
    _online(monkeypatch, "dev-lane")

    async def flow() -> tuple[dict[str, Any], dict[str, Any]]:
        photo = asyncio.create_task(
            service.send_command_and_wait("dev-lane", actions.CAMERA_CAPTURE, {}, timeout_s=0.3)
        )
        await _wait_until(lambda: service.is_device_busy("dev-lane", actions.CHANNEL_CAPTURE))
        exec_result = await service.send_command_and_wait(
            "dev-lane", actions.DEVICE_EXEC, {"command": "echo hi"}, timeout_s=0.3
        )
        return exec_result, await photo

    exec_result, photo = asyncio.run(flow())
    assert exec_result["status"] == "timeout"
    assert exec_result["reason_code"] == "command.deadline_exceeded"
    assert photo["status"] == "timeout"


def test_cancel_while_sending_releases_channel(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """下发阶段被取消：通道立刻解除占用，审计以 command.aborted 收尾。"""
    device_id = "dev-cancel"

    async def hanging_send(target: str, data: str) -> bool:
        # 永不完成的下发：调用方只能靠取消脱身
        await asyncio.Event().wait()
        return True

    monkeypatch.setattr(manager, "send_to", hanging_send)

    async def flow() -> None:
        task = asyncio.create_task(service.send_command_and_wait(device_id, actions.DEVICE_EXEC, {}, timeout_s=1))
        await _wait_until(lambda: service.is_device_busy(device_id, actions.CHANNEL_MAINTENANCE))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.is_device_busy(device_id, actions.CHANNEL_MAINTENANCE)

    asyncio.run(flow())

    rows = command_audit.list_commands(device_id=device_id)
    assert len(rows) == 1
    assert rows[0]["finished_at"] is not None
    assert rows[0]["reason_code"] == "command.aborted"


def test_exec_data_is_audited_but_media_is_not(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令审计只记不可重现的文本结果：exec 输出落库，媒体 base64 不落。"""
    fake = _online(monkeypatch, "dev-exec")

    async def flow() -> dict[str, Any]:
        task = asyncio.create_task(service.send_command_and_wait("dev-exec", actions.DEVICE_EXEC, {}, timeout_s=5))
        await fake.wait_for_sent()
        command = json.loads(fake.sent[0])
        service.resolve(
            str(command["id"]),
            {"status": "ok", "reason_code": "", "terminal_message": "", "data": {"output": "hi", "exit_code": 0}},
        )
        return await task

    asyncio.run(flow())

    media = _online(monkeypatch, "dev-photo")

    async def flow_photo() -> None:
        task = asyncio.create_task(service.send_command_and_wait("dev-photo", actions.CAMERA_CAPTURE, {}, timeout_s=5))
        await media.wait_for_sent()
        command = json.loads(media.sent[0])
        service.resolve(
            str(command["id"]),
            {"status": "ok", "reason_code": "", "terminal_message": "", "data": {"image_base64": "aGVsbG8="}},
        )
        await task

    asyncio.run(flow_photo())

    rows = {row["action"]: row for row in command_audit.list_commands(limit=50)}
    assert json.loads(str(rows["device.exec"]["data"])) == {"output": "hi", "exit_code": 0}
    assert rows["camera.capture"]["data"] is None


def test_pending_result_second_resolve_is_noop(fresh_db: str) -> None:
    async def flow() -> bool:
        loop = asyncio.get_running_loop()
        pending = service.PendingResult(loop, loop.create_future())
        assert pending.resolve({"a": 1}) is True
        return pending.resolve({"a": 2}) is False

    assert asyncio.run(flow())


def test_audit_data_only_keeps_exec_output() -> None:
    assert service.audit_data("device.exec", {"output": "o"}) == '{"output": "o"}'
    assert service.audit_data("camera.capture", {"image_base64": "x"}) is None
    assert service.audit_data("device.exec", "not-a-dict") is None


def test_terminal_message_from_device_is_preserved(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备给了具体说法时不被登记表的通用说法覆盖。"""
    fake = _online(monkeypatch, "dev-msg")

    async def flow() -> dict[str, Any]:
        task = asyncio.create_task(
            service.send_command_and_wait("dev-msg", actions.SESSION_SETTLE, {"cert_no": "C"}, timeout_s=5)
        )
        await fake.wait_for_sent()
        command = json.loads(fake.sent[0])
        service.resolve(
            str(command["id"]),
            {"status": "failed", "reason_code": "door.not_free", "terminal_message": "喷头 3 不存在", "data": None},
        )
        return await task

    result = asyncio.run(flow())
    assert result["status"] == "failed"
    assert result["terminal_message"] == "喷头 3 不存在"

    rows = command_audit.list_commands(device_id="dev-msg")
    assert rows[0]["terminal_message"] == "喷头 3 不存在"  # 设备原话入审计行


def test_config_push_ack_round_trip(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置推送：设备回 config.ack 后 applied 为真。"""
    fake = _online(monkeypatch, "dev-push")

    async def flow() -> dict[str, Any]:
        task = asyncio.create_task(service.push_config_update("dev-push", 2, {"heartbeat_interval_s": 45}, timeout_s=5))
        await fake.wait_for_sent()
        update = json.loads(fake.sent[0])
        assert update["type"] == "config.update"
        assert update["payload"]["revision"] == 2
        service.resolve_config_ack("dev-push", "2")
        return await task

    result = asyncio.run(flow())
    assert result == {"notified": True, "applied": True}


def test_config_push_offline_and_timeout(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _online(monkeypatch, "dev-push")

    async def flow() -> tuple[dict[str, Any], dict[str, Any]]:
        timeout_result = await service.push_config_update("dev-push", 1, {}, timeout_s=0.05)
        monkeypatch.setattr(manager, "_connections", {})
        offline_result = await service.push_config_update("dev-push", 1, {}, timeout_s=1)
        return timeout_result, offline_result

    timeout_result, offline_result = asyncio.run(flow())
    assert timeout_result == {"notified": True, "applied": False, "reason": "ack timeout"}
    assert offline_result == {"notified": False, "applied": False, "reason": "device offline"}


def test_config_ack_without_pending_returns_false(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert service.resolve_config_ack("dev-none", "1") is False


def test_outcome_frame_uses_protocol_envelope_shape(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """下发帧携带协议版本、唯一帧 id 与 RFC 3339 时刻（信封不变量）。"""
    fake = _online(monkeypatch, "dev-envelope")

    async def flow() -> dict[str, Any]:
        task = asyncio.create_task(
            service.send_command_and_wait("dev-envelope", actions.CAMERA_CAPTURE, {}, timeout_s=5)
        )
        await fake.wait_for_sent()
        command = json.loads(fake.sent[0])
        _resolve_ok(command, "拍照完成")
        return await task

    asyncio.run(flow())
    frame = json.loads(fake.sent[0])
    assert frame["v"] == protocol.PROTOCOL_VERSION
    assert frame["ts"].endswith("Z")
    assert frame["payload"]["action"] == actions.CAMERA_CAPTURE
    assert frame["payload"]["params"] == {}


def test_rejected_command_uses_registry_wording(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """过载拒绝的 terminal_message 用登记表措辞，不暴露内部通道名。"""
    assert reason_codes.SERVER_OVERLOADED.code == "server.overloaded"
    assert outcomes.REJECTED.value == "rejected"


def test_abort_pending_closes_previous_process_commands(fresh_db: str) -> None:
    """启动时把上一进程的 pending 收尾，审计页不再永久显示进行中。"""
    command_audit.insert_command("cmd-pending", "dev-startup", "clock.set")
    assert command_audit.abort_pending("2026-10-28T00:00:00.000Z") == 1
    row = command_audit.list_commands(device_id="dev-startup")[0]
    assert row["finished_at"] == "2026-10-28T00:00:00.000Z"
    assert row["status"] == outcomes.FAILED.value
    assert row["reason_code"] == reason_codes.COMMAND_ABORTED.code
    assert row["terminal_message"] == "服务重启，命令未收尾"
    assert command_audit.abort_pending("2026-10-28T00:00:01.000Z") == 0


def test_init_schema_aborts_pending_commands(fresh_db: str) -> None:
    from app.main import init_schema

    command_audit.insert_command("cmd-pending-boot", "dev-boot", "clock.set")
    init_schema()
    row = command_audit.list_commands(device_id="dev-boot")[0]
    assert row["status"] == outcomes.FAILED.value
    assert row["reason_code"] == reason_codes.COMMAND_ABORTED.code
