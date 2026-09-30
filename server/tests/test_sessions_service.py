"""sessions 服务层测试：命令编排、配置推送、离线路径（纯 asyncio，无 HTTP）。"""

import asyncio
import json

import pytest

from app.gateway import manager as manager_module
from app.modules.sessions import service


class FakeWS:
    """假设备连接：捕获发送帧，供测试断言。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> bool:
        self.sent.append(json.loads(data))
        return True


def _setup(monkeypatch: pytest.MonkeyPatch, device_id: str) -> FakeWS:
    ws = FakeWS()
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: ws})
    return ws


def test_send_command_and_wait_ok(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    device_id = "dev-x"
    ws = _setup(monkeypatch, device_id)

    async def flow() -> dict:
        task = asyncio.create_task(
            service.send_command_and_wait(device_id, "session.assign", {"door_no": 1}, timeout_s=5)
        )
        await asyncio.sleep(0.05)
        cmd = ws.sent[0]
        assert cmd["type"] == "session.command"
        assert cmd["payload"]["action"] == "session.assign"
        service.resolve(cmd["id"], {"status": "ok", "reason_code": "", "terminal_message": "已分配", "data": None})
        return await task

    result = asyncio.run(flow())
    assert result["status"] == "ok"
    assert result["terminal_message"] == "已分配"


def test_send_command_offline(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, "dev-x")
    monkeypatch.setattr(manager_module.manager, "_connections", {})  # 设备不在线

    async def flow() -> dict:
        return await service.send_command_and_wait("dev-x", "session.assign", {}, timeout_s=5)

    result = asyncio.run(flow())
    assert result == {
        "status": "timeout",
        "reason_code": "device.offline",
        "terminal_message": "设备离线",
        "data": None,
    }


def test_send_command_timeout(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, "dev-x")  # 在线但不回报

    async def flow() -> dict:
        return await asyncio.wait_for(
            service.send_command_and_wait("dev-x", "session.assign", {}, timeout_s=0.2), timeout=5
        )

    result = asyncio.run(flow())
    assert result["status"] == "timeout"
    assert result["reason_code"] == "command.deadline_exceeded"


def test_push_config_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    device_id = "dev-x"
    ws = _setup(monkeypatch, device_id)

    async def flow() -> dict:
        task = asyncio.create_task(service.push_config_update(device_id, 2, {"heartbeat_interval_s": 45}, timeout_s=5))
        await asyncio.sleep(0.05)
        upd = ws.sent[0]
        assert upd["type"] == "config.update"
        service.resolve_config_ack(device_id, "2")
        return await task

    result = asyncio.run(flow())
    assert result == {"notified": True, "applied": True}


def test_push_config_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.manager, "_connections", {})

    async def flow() -> dict:
        return await service.push_config_update("dev-x", 1, {"heartbeat_interval_s": 45}, timeout_s=5)

    result = asyncio.run(flow())
    assert result == {"notified": False, "applied": False, "reason": "device offline"}
