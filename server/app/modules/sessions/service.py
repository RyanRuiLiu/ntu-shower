"""命令下发与结果等待：asyncio Future + 超时（单 worker 语义下安全）。"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import TypedDict

from app.core import protocol
from app.core.clock import rfc3339_after
from app.core.config import settings
from app.core.protocol import JsonObject
from app.gateway.manager import manager
from app.modules.sessions import repository

DEFAULT_TIMEOUT_S = 30.0


class CommandOutcome(TypedDict):
    """命令下发结果，形状对应 protocol/schema/session.command_result.json。"""

    status: str
    reason_code: str
    terminal_message: str
    data: JsonObject | None


class ConfigPushResult(TypedDict, total=False):
    """配置推送结果；reason 仅在下发或确认失败时出现。"""

    notified: bool
    applied: bool
    reason: str


@dataclass
class PendingResult:
    """挂起中的跨线程可回填 Future（resolve 可能来自其他线程/事件循环）。"""

    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[JsonObject]

    def resolve(self, value: JsonObject) -> bool:
        if self.future.done():
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.future.set_result(value)
        else:
            self.loop.call_soon_threadsafe(_set, self.future, value)
        return True


def _set(future: asyncio.Future[JsonObject], value: JsonObject) -> None:
    if not future.done():
        future.set_result(value)


# command_id → 挂起中的命令结果
_pending: dict[str, PendingResult] = {}

# device_id → 在途命令（设备端自动化严格串行：同一设备同时只允许一条在途命令，
# 第二条会被拒绝而非排队——排队会让后续命令与前一条的 UI 流程打架）
_device_pending: dict[str, str] = {}

# device_id → 挂起中的配置确认（一设备同时至多一个待确认推送，后到的覆盖先到的）
_config_pending: dict[str, PendingResult] = {}


def is_device_busy(device_id: str) -> bool:
    return device_id in _device_pending


async def send_command_and_wait(
    device_id: str,
    action: str,
    params: JsonObject,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CommandOutcome:
    """下发 session.command 并等待 session.command_result 或超时。

    设备已有在途命令时立即返回 rejected（device.busy），不排队。
    """
    command_id = str(uuid.uuid4())
    if device_id in _device_pending:
        repository.insert(command_id, device_id, action)
        repository.finish(command_id, "rejected", "device.busy", "")
        return {
            "status": "rejected",
            "reason_code": "device.busy",
            "terminal_message": "设备正在执行其他命令",
            "data": None,
        }
    _device_pending[device_id] = command_id

    loop = asyncio.get_running_loop()
    pending = PendingResult(loop, loop.create_future())
    _pending[command_id] = pending

    envelope = protocol.envelope(
        "session.command",
        {
            "action": action,
            "params": params,
            "deadline_ts": rfc3339_after(timeout_s),
        },
        frame_id=command_id,
    )
    repository.insert(command_id, device_id, action)
    sent = await manager.send_to(device_id, envelope)
    if not sent:
        _device_pending.pop(device_id, None)
        _pending.pop(command_id, None)
        repository.finish(command_id, "timeout", "device.offline", "device offline")
        return {
            "status": "timeout",
            "reason_code": "device.offline",
            "terminal_message": "设备离线",
            "data": None,
        }

    try:
        result = await asyncio.wait_for(pending.future, timeout=timeout_s)
        outcome = CommandOutcome(
            status=str(result.get("status", "failed")),
            reason_code=str(result.get("reason_code", "")),
            terminal_message=str(result.get("terminal_message", "")),
            data=result.get("data"),
        )
    except asyncio.TimeoutError:
        outcome = CommandOutcome(
            status="timeout",
            reason_code="command.deadline_exceeded",
            terminal_message="",
            data=None,
        )
    finally:
        _device_pending.pop(device_id, None)
        _pending.pop(command_id, None)

    repository.finish(command_id, outcome["status"], outcome["reason_code"], outcome["terminal_message"])
    return outcome


def resolve(command_id: str, payload: JsonObject) -> bool:
    """网关收到 session.command_result 时回填挂起中的 Future。"""
    pending = _pending.pop(command_id, None)
    if pending is None:
        return False
    return pending.resolve(payload)


async def push_config_update(
    device_id: str,
    revision: int,
    config: JsonObject,
    timeout_s: float | None = None,
) -> ConfigPushResult:
    """向单台设备推送 config.update 并等待 config.ack。"""
    if timeout_s is None:
        timeout_s = settings.config_ack_timeout_s
    loop = asyncio.get_running_loop()
    pending = PendingResult(loop, loop.create_future())
    _config_pending[device_id] = pending

    envelope = protocol.envelope(
        "config.update",
        {"revision": revision, "config": config},
    )
    sent = await manager.send_to(device_id, envelope)
    if not sent:
        _config_pending.pop(device_id, None)
        return ConfigPushResult(notified=False, applied=False, reason="device offline")
    try:
        await asyncio.wait_for(pending.future, timeout=timeout_s)
        return ConfigPushResult(notified=True, applied=True)
    except asyncio.TimeoutError:
        _config_pending.pop(device_id, None)
        return ConfigPushResult(notified=True, applied=False, reason="ack timeout")


def resolve_config_ack(device_id: str, revision: str) -> bool:
    """网关收到 config.ack 时回填挂起中的配置推送。"""
    pending = _config_pending.pop(device_id, None)
    if pending is None:
        return False
    return pending.resolve({"revision": revision})
