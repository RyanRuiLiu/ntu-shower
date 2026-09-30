"""设备能力集（capabilities）的作用：下发前预检，避免把设备不认识的命令发过去。"""

import asyncio

from fastapi.testclient import TestClient

from app.modules.devices import repository as devices_repository
from app.modules.sessions import service as sessions_service


def _register(client: TestClient, device_id: str, capabilities: list[str]) -> None:
    devices_repository.register(device_id, "0.3.3", 10, "4.4.4", capabilities, "127.0.0.1")
    devices_repository.set_trusted(device_id, True)


def test_unsupported_action_is_rejected_before_sending(client: TestClient) -> None:
    """设备声明只支持 assign 时，settle 在下发前就被拒（不等设备回 unknown_action）。"""
    device_id = "dev-caps-narrow"
    _register(client, device_id, ["session.assign"])

    outcome = asyncio.run(
        sessions_service.send_command_and_wait(device_id, "session.settle", {"cert_no": "C"}, timeout_s=0.1),
    )

    assert outcome["status"] == "failed"
    assert outcome["reason_code"] == "session.unsupported_by_device"


def test_supported_action_is_not_blocked_by_the_precheck(client: TestClient) -> None:
    """声明的 action 不被预检拦下：应走到真实的设备下发（此处在网为空 → timeout）。"""
    device_id = "dev-caps-exact"
    _register(client, device_id, ["session.assign"])

    outcome = asyncio.run(
        sessions_service.send_command_and_wait(device_id, "session.assign", {"cert_no": "C"}, timeout_s=0.1),
    )

    assert outcome["reason_code"] != "session.unsupported_by_device"


def test_empty_capabilities_means_undeclared_and_passes(client: TestClient) -> None:
    """空能力集表示"未声明"（旧版本设备）：不得据此拦截，否则升级会锁死在网设备。"""
    device_id = "dev-caps-empty"
    _register(client, device_id, [])

    outcome = asyncio.run(
        sessions_service.send_command_and_wait(device_id, "session.settle", {"cert_no": "C"}, timeout_s=0.1),
    )

    assert outcome["reason_code"] != "session.unsupported_by_device"


def test_unknown_device_has_no_capability_constraint(client: TestClient) -> None:
    """设备不存在时能力集为空 → 不因能力被拒（离线由守卫负责报告）。"""
    outcome = asyncio.run(
        sessions_service.send_command_and_wait("dev-caps-ghost", "session.assign", {"cert_no": "C"}, timeout_s=0.1),
    )

    assert outcome["reason_code"] != "session.unsupported_by_device"
