"""楼层状态行：汇总各设备的空闲喷头数，推给设备上的那一行文字。

设备自己只能读本机 `t_doorlist`，看不到别的楼层。汇总只在服务端做一次，每台设备
收到的是同一份现态，而不是各自去问别人。

## 口径

- **可用 = 已信任且有活动连接**。未信任的设备不处理业务帧，它库里的喷头行是上次
  信任期间的残留；拿它当在线会把陈旧读数说成现态。
- `free` 只数 `DoorState.FREE`。已选中不算可用（下一个人得先消掉选择），维修中与
  已禁用更不算。
- **在线但没上报过任何喷头的设备给 `free = null`**，设备端显示「无数据」。给 0 会
  把「不知道」说成「满了」。
- 顺序沿用设备清单的展示顺序（管理员排的 `sort_order`），与设备下拉、设备表同序。
- **接收方 = 已信任且有活动连接**，与 `config.update` / `device.update` 同一条口径；
  刚上线、还没上报喷头的设备也收帧，它自己的楼层在首次采集前显示「无数据」，其余
  楼层立刻可见。

## 推送形态

`status_line.update` 是现态，不是事件：不排队、不回执、不补发。每轮比对整份载荷，
变了就推，没变则每 [KEEPALIVE_S] 秒重发一次——设备据此判断这条数据还活着，超过
三倍保活时长没收到就自行隐藏。链路断开时设备端立即隐藏，重连后的第一轮推送恢复。
旧版 APK 不认识这条消息，按既有规则忽略，因此不需要能力协商。
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import protocol
from app.core.connections import manager
from app.core.logging import log_event
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState

#: 汇总间隔。门的迁移本身是秒级事件，一轮一秒足够新；更密只会重复算同一份现态。
PUSH_INTERVAL_S = 1.0

#: 没有变化时的保活重发间隔。设备端据此判定状态行是否过期，取三倍余量（90 秒）。
KEEPALIVE_S = 30.0

#: 汇总持续失败时，日志最多这么久记一条——库忙时每秒一条会把真正的问题淹掉。
FAILURE_LOG_INTERVAL_S = 60.0


@dataclass(frozen=True)
class _Recipient:
    """这一轮该收帧的设备，以及它当前连接的身份代数。"""

    device_id: str
    generation: int


@dataclass(frozen=True)
class _Snapshot:
    """一轮汇总：发给所有人的载荷，以及这一轮该收帧的设备。"""

    payload: dict[str, Any]
    recipients: tuple[_Recipient, ...]


def _snapshot(session: Session) -> _Snapshot:
    """读一次设备与喷头现态，拼出状态行载荷。

    读库在各领域自己的 service 里完成（设备域、门域），本模块不碰表。
    """
    floors: list[dict[str, Any]] = []
    recipients: list[_Recipient] = []
    for record in devices_service.list_devices(session):
        device_id = str(record["device_id"])
        online = bool(record["online"]) and bool(record["trusted"])
        # 只要现态，不要 `list_doors` 那一路带出的上次使用：状态行数的是 door_live，
        # 每台设备每秒少一次联表查询。
        live = doors_service.live_doors(device_id)
        free = sum(1 for door in live.values() if door.state == DoorState.FREE.value) if online and live else None
        floors.append(
            {
                "device_id": device_id,
                "name": str(record["name"]) or device_id,
                "online": online,
                "free": free,
            }
        )
        if online:
            recipients.append(_Recipient(device_id, manager.generation_of(device_id)))
    return _Snapshot({"floors": floors}, tuple(recipients))


class _Pusher:
    """一轮推送的全部状态；`tick()` 是「汇总 → 按需推送」的一轮。

    生命周期由 core/background 管理，本模块只暴露 `PUSH_INTERVAL_S` 与一个
    协程入口，不再持有自己的循环与取消逻辑。
    """

    def __init__(self) -> None:
        self.last_payload: dict[str, Any] | None = None
        self.last_sent: dict[str, float] = {}
        self.last_connection: dict[str, int] = {}
        self.last_failure_log = 0.0

    async def tick(self, session: Session) -> None:
        loop = asyncio.get_running_loop()
        try:
            snapshot = await run_in_threadpool(_snapshot, session)
        except Exception as exc:  # noqa: BLE001 —— 一轮读库失败退给统一循环，下一轮重试
            now = loop.time()
            if now - self.last_failure_log >= FAILURE_LOG_INTERVAL_S:
                self.last_failure_log = now
                log_event("ERROR", "status_line.snapshot-failed", "楼层状态汇总失败，下一轮重试", error=str(exc))
            return

        now = loop.time()
        changed = snapshot.payload != self.last_payload
        current = {recipient.device_id for recipient in snapshot.recipients}
        for device_id in set(self.last_sent) | set(self.last_connection):
            if device_id not in current:
                self.last_sent.pop(device_id, None)
                self.last_connection.pop(device_id, None)
        for recipient in snapshot.recipients:
            device_id = recipient.device_id
            fresh_connection = self.last_connection.get(device_id) == recipient.generation
            if not changed and fresh_connection and now - self.last_sent.get(device_id, 0.0) < KEEPALIVE_S:
                continue
            try:
                sent = await manager.send_to(device_id, protocol.envelope("status_line.update", snapshot.payload))
            except Exception as exc:  # noqa: BLE001 —— 一条坏连接不影响其余设备
                log_event(
                    "WARNING",
                    "status_line.push-failed",
                    "楼层状态推送异常",
                    device_id=device_id,
                    error=str(exc),
                )
                sent = False
            if sent:
                self.last_sent[device_id] = now
                self.last_connection[device_id] = recipient.generation
        self.last_payload = snapshot.payload


pusher = _Pusher()


async def tick(session: Session) -> None:
    """统一循环的一轮入口（Background 每轮传入短会话）。"""
    await pusher.tick(session)
