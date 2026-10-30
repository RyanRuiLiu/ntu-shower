"""命令下发与结果等待：asyncio Future 加超时（单 worker 语义下安全）。"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core import actions, outcomes, protocol, reason_codes
from app.core.config import settings
from app.core.connections import manager
from app.core.logging import log_event, trace_id_var
from app.core.protocol import JsonObject
from app.modules.audit import repository as command_audit
from app.modules.devices import service as devices_service

from .schemas import CommandOutcome, ConfigPushResult

DEFAULT_TIMEOUT_S = 30.0


def _with_message(outcome: CommandOutcome) -> CommandOutcome:
    """设备没给中文说明时用登记表补齐，保证 terminal_message 可直接显示。

    只在为空时补：设备端给的具体说法比登记表的通用说法更有用。
    """
    if outcome["terminal_message"] or not outcome["reason_code"]:
        return outcome
    return CommandOutcome(
        status=outcome["status"],
        reason_code=outcome["reason_code"],
        terminal_message=reason_codes.describe(outcome["reason_code"]),
        data=outcome["data"],
    )


@dataclass
class PendingResult:
    """挂起中的跨线程可回填 Future（resolve 可能来自其他线程或事件循环）。"""

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

# 通道排队状态。设备端把命令分到几条互不阻塞的路径（见 core/actions 的执行通道），
# 服务端按同一划分排队：同一通道内每台设备串行，跨通道并行。限流只针对过载，
# 不针对串行——串行由通道锁承担。
#: (device_id, channel) → 正在等这条通道的命令数（含正在执行的那条）。
_channel_waiting: dict[tuple[str, str], int] = {}

#: (device_id, channel) → 串行锁。同通道的命令在这里排队。
_channel_locks: dict[tuple[str, str], asyncio.Lock] = {}

#: 同通道允许排队的命令数上限。无限增长时后排命令必然已超客户端超时，不如立刻
#: 告知调用方过载，让它换个时间再试。
#:
#: 取值依据：一条待投递播报在 voice 通道上至多占 1 个排队位（prepare 先行、play
#: 随后），喷头最多的设备全部同时迁移就是 8 场；低于它，语音风暴时后排播报会被
#: 当成过载拒掉。与设备端 SerialCommandQueue.DEFAULT_MAX_PENDING 同值。
CHANNEL_QUEUE_MAX = 8

# device_id → 挂起中的配置确认（一设备同时至多一个待确认推送，后到覆盖先到）
_config_pending: dict[str, PendingResult] = {}


def is_device_busy(device_id: str, channel: str) -> bool:
    """该设备在该通道上是否正在执行命令。仅用于观测与测试；限流靠排队实现。"""
    return _channel_waiting.get((device_id, channel), 0) > 0


def _lock_for(device_id: str, channel: str) -> asyncio.Lock:
    """取该设备该通道的串行锁，没有就建一个。"""
    key = (device_id, channel)
    lock = _channel_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[key] = lock
    return lock


@contextlib.asynccontextmanager
async def _acquire_with_deadline(lock: asyncio.Lock, deadline: float) -> AsyncIterator[None]:
    """在截止时刻前取得通道锁；超时抛 asyncio.TimeoutError。

    不用 `wait_for(lock.acquire())`：Python 3.10 的 `wait_for` 在取消与锁已取得的
    边界上会吞掉取消并继续执行，调用方明明取消了命令，命令却仍被下发、锁仍被占住。
    这里显式持有获取任务，取消与超时都先收尾再抛出。
    """
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise asyncio.TimeoutError
    acquire = asyncio.ensure_future(lock.acquire())
    try:
        done, _ = await asyncio.wait({acquire}, timeout=remaining)
    except asyncio.CancelledError:
        acquire.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await acquire
        if acquire.done() and not acquire.cancelled() and acquire.result():
            lock.release()
        raise
    if not done:
        acquire.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await acquire
        if acquire.done() and not acquire.cancelled() and acquire.result():
            lock.release()
        raise asyncio.TimeoutError
    try:
        yield
    finally:
        lock.release()


def _supported_actions(device_id: str) -> list[str]:
    """设备在 link.hello 中声明的能力集合；未声明或设备不存在时为空。"""
    device = devices_service.get_device(device_id)
    if device is None:
        return []
    capabilities = device.get("capabilities")
    return capabilities if isinstance(capabilities, list) else []


async def send_command_and_wait(
    device_id: str,
    action: str,
    params: JsonObject,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CommandOutcome:
    """下发命令并等待结果；出口保证 terminal_message 有中文，可原样显示。"""
    return _with_message(await _send_and_wait(device_id, action, params, timeout_s))


async def _send_and_wait(
    device_id: str,
    action: str,
    params: JsonObject,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CommandOutcome:
    """下发 session.command 并等待结果或超时。

    同通道内串行排队，后来者等前面那条做完；仅当队列满时拒绝（过载信号）。
    """
    command_id = str(uuid.uuid4())
    supported = _supported_actions(device_id)
    # 空能力集表示设备未声明：不据此拦截，否则升级服务端会锁死在网设备
    if supported and action not in supported:
        command_audit.insert_command(
            command_id, device_id, action, trace_id_var.get(), json.dumps(params, ensure_ascii=False)
        )
        command_audit.finish(command_id, outcomes.FAILED.value, reason_codes.DEVICE_UNSUPPORTED_ACTION.code, "")
        return {
            "status": outcomes.FAILED.value,
            "reason_code": reason_codes.DEVICE_UNSUPPORTED_ACTION.code,
            "terminal_message": f"设备不支持该操作：{action}",
            "data": None,
        }

    channel = actions.ACTION_CHANNELS[action]
    key = (device_id, channel)
    if _channel_waiting.get(key, 0) >= CHANNEL_QUEUE_MAX:
        command_audit.insert_command(
            command_id, device_id, action, trace_id_var.get(), json.dumps(params, ensure_ascii=False)
        )
        log_event(
            "INFO",
            "command.rejected",
            "命令被拒：通道待处理已满",
            command_id=command_id,
            device_id=device_id,
            action=action,
            channel=channel,
        )
        command_audit.finish(command_id, outcomes.REJECTED.value, reason_codes.SERVER_OVERLOADED.code, "")
        return {
            "status": outcomes.REJECTED.value,
            "reason_code": reason_codes.SERVER_OVERLOADED.code,
            # 用登记表里的措辞，不自己拼：拼出来的会把内部通道名摊给用户
            "terminal_message": reason_codes.SERVER_OVERLOADED.message,
            "data": None,
        }

    _channel_waiting[key] = _channel_waiting.get(key, 0) + 1
    loop = asyncio.get_running_loop()
    # 排队与执行共用一个时间预算：等锁的时间也算在内
    deadline = loop.time() + timeout_s
    try:
        async with _acquire_with_deadline(_lock_for(device_id, channel), deadline):
            remaining = deadline - loop.time()
            if remaining <= 0:
                command_audit.insert_command(
                    command_id, device_id, action, trace_id_var.get(), json.dumps(params, ensure_ascii=False)
                )
                command_audit.finish(command_id, outcomes.TIMEOUT.value, reason_codes.DEADLINE_EXCEEDED.code, "")
                return CommandOutcome(
                    status=outcomes.TIMEOUT.value,
                    reason_code=reason_codes.DEADLINE_EXCEEDED.code,
                    terminal_message="",
                    data=None,
                )
            return await _dispatch(command_id, device_id, action, params, remaining)
    except asyncio.TimeoutError:
        command_audit.insert_command(
            command_id, device_id, action, trace_id_var.get(), json.dumps(params, ensure_ascii=False)
        )
        command_audit.finish(command_id, outcomes.TIMEOUT.value, reason_codes.DEADLINE_EXCEEDED.code, "")
        return CommandOutcome(
            status=outcomes.TIMEOUT.value,
            reason_code=reason_codes.DEADLINE_EXCEEDED.code,
            terminal_message="",
            data=None,
        )
    finally:
        left = _channel_waiting.get(key, 1) - 1
        if left > 0:
            _channel_waiting[key] = left
        else:
            _channel_waiting.pop(key, None)


async def _dispatch(
    command_id: str,
    device_id: str,
    action: str,
    params: JsonObject,
    timeout_s: float,
) -> CommandOutcome:
    """真正下发并等结果。调用方已持有该通道的锁，同通道不会并发进来。"""
    loop = asyncio.get_running_loop()
    pending = PendingResult(loop, loop.create_future())
    _pending[command_id] = pending
    started_at = loop.time()
    # 协程被取消时它是最终状态：审计记为 aborted，不留永不收尾的记录
    outcome = CommandOutcome(
        status=outcomes.FAILED.value,
        reason_code=reason_codes.COMMAND_ABORTED.code,
        terminal_message="",
        data=None,
    )

    try:
        envelope = protocol.envelope(
            "session.command",
            {
                "action": action,
                "params": params,
                # 相对时长而非绝对时刻：设备无 NTP，传绝对时刻会让设备时钟偏差直接
                # 导致所有命令被判过期。设备按"从收到算起"判定，挡住的是在设备侧
                # 队列里等太久的命令。
                "timeout_ms": int(timeout_s * 1000),
            },
            frame_id=command_id,
        )
        command_audit.insert_command(
            command_id, device_id, action, trace_id_var.get(), json.dumps(params, ensure_ascii=False)
        )
        if not await manager.send_to(device_id, envelope):
            log_event(
                "WARNING",
                "command.dropped-offline",
                "设备离线，命令未投出",
                command_id=command_id,
                device_id=device_id,
                action=action,
            )
            outcome = CommandOutcome(
                status=outcomes.TIMEOUT.value,
                reason_code=reason_codes.DEVICE_OFFLINE.code,
                terminal_message="设备离线",
                data=None,
            )
            return outcome

        log_event(
            "INFO",
            "command.sent",
            "命令已投出",
            command_id=command_id,
            device_id=device_id,
            action=action,
            timeout_s=timeout_s,
        )
        try:
            result = await asyncio.wait_for(pending.future, timeout=timeout_s)
        except asyncio.TimeoutError:
            outcome = CommandOutcome(
                status=outcomes.TIMEOUT.value,
                reason_code=reason_codes.DEADLINE_EXCEEDED.code,
                terminal_message="",
                data=None,
            )
        else:
            outcome = CommandOutcome(
                status=str(result.get("status", outcomes.FAILED.value)),
                reason_code=str(result.get("reason_code", "")),
                terminal_message=str(result.get("terminal_message", "")),
                data=result.get("data"),
            )
        return outcome
    finally:
        # 清理覆盖整个 await 周期：登记发生在下发之前，下发本身是 await 点，调用方
        # 在此期间断开会取消整个协程，挂起项只能靠 finally 摘除。
        _pending.pop(command_id, None)
        elapsed_ms = int((loop.time() - started_at) * 1000)
        log_event(
            "INFO",
            "command.finished",
            "命令收尾",
            command_id=command_id,
            device_id=device_id,
            action=action,
            status=str(outcome["status"]),
            reason_code=str(outcome["reason_code"] or "-"),
            elapsed_ms=elapsed_ms,
        )
        # 审计行与函数出口同用登记表补齐规则：失败行 terminal_message 不为空。
        # 补齐在此处做而不是只靠 _with_message，否则纯超时的审计行会留空。
        terminal_message = outcome["terminal_message"] or (
            reason_codes.describe(outcome["reason_code"]) if outcome["reason_code"] else ""
        )
        command_audit.finish(
            command_id,
            outcome["status"],
            outcome["reason_code"],
            terminal_message,
            audit_data(action, outcome["data"]),
        )


def audit_data(action: str, data: object) -> str | None:
    """命令结果里进审计的 data：只记不可重现的文本结果。

    device.exec 的输出是不可重现的运维事实，必须留痕，体积有配置上限；
    媒体（截图、拍照的 base64）与 ui.dump 的 XML 属于数据重型且可重取，不落。
    分配与结算的参数由 HTTP 审计的请求体留痕，命令审计只记结果。
    """
    if action != actions.DEVICE_EXEC or not isinstance(data, dict):
        return None
    return json.dumps(data, ensure_ascii=False)


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

    try:
        envelope = protocol.envelope(
            "config.update",
            {"revision": revision, "config": config},
        )
        if not await manager.send_to(device_id, envelope):
            return ConfigPushResult(notified=False, applied=False, reason="device offline")
        try:
            await asyncio.wait_for(pending.future, timeout=timeout_s)
            return ConfigPushResult(notified=True, applied=True)
        except asyncio.TimeoutError:
            return ConfigPushResult(notified=True, applied=False, reason="ack timeout")
    finally:
        # 清理覆盖整个 await 周期，理由与 _dispatch 相同；成功路径已由
        # resolve_config_ack 摘除，这里再摘一次是幂等的。
        _config_pending.pop(device_id, None)


def resolve_config_ack(device_id: str, revision: int) -> bool:
    """网关收到 config.ack 时回填挂起中的配置推送。"""
    pending = _config_pending.pop(device_id, None)
    if pending is None:
        return False
    return pending.resolve({"revision": revision})
