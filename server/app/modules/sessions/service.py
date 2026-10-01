"""命令下发与结果等待：asyncio Future + 超时（单 worker 语义下安全）。"""

import asyncio
import uuid
from dataclasses import dataclass

from app.core import actions, outcomes, protocol, reason_codes
from app.core.config import settings
from app.core.logging import log_event, trace_id_var
from app.core.protocol import JsonObject
from app.gateway.manager import manager
from app.modules.devices import service as devices_service
from app.modules.sessions import repository

from .schemas import CommandOutcome, ConfigPushResult

DEFAULT_TIMEOUT_S = 30.0


def _with_message(outcome: CommandOutcome) -> CommandOutcome:
    """设备没给中文说明时，用登记表补齐。

    前端只显示 `terminal_message`（`reason_code` 是给机器分支用的），所以它不能是
    空的——设备端偶尔漏给（内部错误那种），补在这里比让界面显示空白强。

    只在**空**的时候补：设备端给的具体说法（"喷头 3 不存在"）比登记表里的
    通用说法（"找不到这个喷头"）更有用。
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

# 通道内的排队状态。
#
# 设备端把命令分到几条互不阻塞的路径上跑（划分见 `core/actions.py` 的执行通道一节），
# 服务端按同一划分**排队**：同一通道内每台设备串行执行，不同通道互不影响。
#
# 此前是"同通道已有在途就拒绝"，那个粒度是错的——它连**正常的先后操作**都拒。拒绝真正
# 该管的是过载，不是串行。而设备端的 `SerialCommandQueue` 本来就是排队、从不丢弃，
# 服务端那层拒绝不但多余，还比设备端更严：设备端有能力做的事被服务端拦掉了。
#: (device_id, channel) → 正在等这条通道的命令数（含正在执行的那条）。
_channel_waiting: dict[tuple[str, str], int] = {}

#: (device_id, channel) → 串行锁。同通道的命令在这里排队。
_channel_locks: dict[tuple[str, str], asyncio.Lock] = {}

#: 同通道允许排队的命令数上限。
#:
#: 上限必须存在：队列无限增长时，排在后面的早就超过客户端的超时了，让它继续等没有
#: 意义——不如立刻告诉调用方现在很挤，它好换个时间再试。
CHANNEL_QUEUE_MAX = 4

# device_id → 挂起中的配置确认（一设备同时至多一个待确认推送，后到的覆盖先到的）
_config_pending: dict[str, PendingResult] = {}


def is_device_busy(device_id: str, channel: str) -> bool:
    """该设备在该通道上是否正在执行命令。

    只用于观测与测试；限流本身靠 [send_command_and_wait] 里的排队，不靠这个判断。
    """
    return _channel_waiting.get((device_id, channel), 0) > 0


def _lock_for(device_id: str, channel: str) -> asyncio.Lock:
    """取该设备该通道的串行锁，没有就建一个。"""
    key = (device_id, channel)
    lock = _channel_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _channel_locks[key] = lock
    return lock


def _supported_actions(device_id: str) -> list[str]:
    """设备在 link.hello 中声明的能力集合（未声明或设备不存在时为空）。"""
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
    """下发命令并等待结果；出口保证 `terminal_message` 有中文。

    调用方（路由、自动结算）拿到的一定是"可以原样显示"的结果，见 [_with_message]。
    """
    return _with_message(await _send_and_wait(device_id, action, params, timeout_s))


async def _send_and_wait(
    device_id: str,
    action: str,
    params: JsonObject,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> CommandOutcome:
    """下发 session.command 并等待 session.command_result 或超时。

    **同通道内串行排队**：后来的等前面那条做完再执行。只有队列满了才拒绝（见
    [CHANNEL_QUEUE_MAX]）——那是过载信号，与"通道正忙"不是一回事。
    """
    command_id = str(uuid.uuid4())
    supported = _supported_actions(device_id)
    # 空能力集表示设备未声明（旧版本）：不据此拦截，否则升级服务端会锁死在网设备
    if supported and action not in supported:
        repository.insert_command(command_id, device_id, action, trace_id_var.get())
        repository.finish(command_id, outcomes.FAILED.value, reason_codes.DEVICE_UNSUPPORTED_ACTION.code, "")
        return {
            "status": outcomes.FAILED.value,
            "reason_code": reason_codes.DEVICE_UNSUPPORTED_ACTION.code,
            "terminal_message": f"设备不支持该操作：{action}",
            "data": None,
        }

    channel = actions.ACTION_CHANNELS[action]
    key = (device_id, channel)
    if _channel_waiting.get(key, 0) >= CHANNEL_QUEUE_MAX:
        repository.insert_command(command_id, device_id, action, trace_id_var.get())
        log_event(
            "INFO",
            "command.rejected",
            "命令被拒：通道待处理已满",
            command_id=command_id,
            device_id=device_id,
            action=action,
            channel=channel,
        )
        repository.finish(command_id, outcomes.REJECTED.value, reason_codes.SERVER_OVERLOADED.code, "")
        return {
            "status": outcomes.REJECTED.value,
            "reason_code": reason_codes.SERVER_OVERLOADED.code,
            # 用登记表里那句话，不自己拼：拼出来的会把内部通道名（automation 之类）摊给用户
            "terminal_message": reason_codes.SERVER_OVERLOADED.message,
            "data": None,
        }

    _channel_waiting[key] = _channel_waiting.get(key, 0) + 1
    loop = asyncio.get_running_loop()
    # 排队与执行共用一个时间预算：等锁的时间也算在内。否则服务端还在等，客户端早超时了
    deadline = loop.time() + timeout_s
    try:
        async with _lock_for(device_id, channel):
            remaining = deadline - loop.time()
            if remaining <= 0:
                repository.insert_command(command_id, device_id, action, trace_id_var.get())
                repository.finish(command_id, outcomes.TIMEOUT.value, reason_codes.DEADLINE_EXCEEDED.code, "")
                return CommandOutcome(
                    status=outcomes.TIMEOUT.value,
                    reason_code=reason_codes.DEADLINE_EXCEEDED.code,
                    terminal_message="",
                    data=None,
                )
            return await _dispatch(command_id, device_id, action, params, remaining)
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
    # 取消时的终值：协程被取消时它就是最终状态，审计记为 aborted 而不是留一行永不收尾的记录
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
                # 相对时长而不是绝对时刻：设备没有 NTP，两边的墙上时钟可能差几秒甚至几分钟。
                # 传绝对时刻会让「设备时钟走得快」这一件事直接导致所有命令被判为已过期——
                # 而那与命令本身还值不值得执行毫无关系。设备按「从收到算起」判定，能挡住
                # 的主要是「排在自己队列里等太久」这种情形，那正是这条限制想管的事。
                "timeout_ms": int(timeout_s * 1000),
            },
            frame_id=command_id,
        )
        repository.insert_command(command_id, device_id, action, trace_id_var.get())
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
        # 清理必须覆盖**整个** await 周期。登记发生在下发之前，而下发本身是 await 点——
        # 调用方在这期间断开连接会取消整个协程，清理若只罩住"等结果"那一段，
        # 挂起项会一直留着。
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
        repository.finish(command_id, outcome["status"], outcome["reason_code"], outcome["terminal_message"])


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
        # 清理覆盖**整个** await 周期，理由与 _dispatch 相同：登记发生在下发之前，而下发
        # 本身是 await 点，调用方在这期间断开连接会取消整个协程，那时既不返回成功也不返回
        # 超时，挂起项只能靠 finally 摘。成功那条已由 resolve_config_ack 摘掉，
        # 这里再摘一次是幂等的。
        _config_pending.pop(device_id, None)


def resolve_config_ack(device_id: str, revision: int) -> bool:
    """网关收到 config.ack 时回填挂起中的配置推送。"""
    pending = _config_pending.pop(device_id, None)
    if pending is None:
        return False
    return pending.resolve({"revision": revision})
