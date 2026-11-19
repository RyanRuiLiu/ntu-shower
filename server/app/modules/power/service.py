"""电源计划域：上报事实、今晚延长（hold）与钟面校正的统一对账器。

## 行业原型

- **计划与例外**：BACnet Schedule——常规夜由设备上报的平台计划自持（MCU 每晚断电、
  清晨上电），hold 是一条只覆盖当夜的例外；
- **用户语义**：恒温器 Hold Until / Resume Schedule——hold 一次性，恢复自动回计划；
- **状态形状**：devices 影子行（AWS IoT Shadow 同型）——上报事实（reported）与
  hold 状态同 row，不另立表；
- **执行模型**：对账（Kubernetes controller）——每 tick 使实际收敛于期望，每一步
  幂等、崩溃重入安全；
- **失效安全**：hold 期间的 MCU 截止是租约（DHCP/etcd lease 同型）——每
  [LEASE_RENEW_INTERVAL_S] 续租一次，租期 [LEASE_TTL_S]；服务端消失，最迟一个
  租期后断电；
- **钟写**：chrony makestep——所有写入都是绝对钟面的一次性 step（校正写准、hold
  施加写"真实+偏移"、恢复写回真实），永不增量拨。

## 为什么偏置期间状态时间仍然正确

墙钟被拨快 offset 后，测量偏差随之变为 −offset + 真实漂移；`to_server(设备读数) =
设备读数 + 测量偏差` 恒等于真实时刻——偏差测量天然吸收偏置，doors 的全部推导
（逐读数平移、交叉校验）不需要任何修改。写钟后的第一帧即收敛：gateway 先用本帧
自带的 `device_clock` + `captured_at` 更新偏差、再派生（见 gateway 的 door.snapshot
分支），不存在漂移窗口。

## 状态先行，审计后置

hold 意图先落库（devices 行），动作随后；审计（job_runs）独立提交，失败绝不回滚
状态——状态迁移被审计的 DB 约束打断，正是本域重写的直接动因。

## 与校正的关系

校正（[_correct_clock]）只在 following_schedule 下进行：hold 期间的偏差是故意的。
互斥是显式的（读 hold 列），不是隐式的域间约定。校正依赖上报的时区（目标钟面按它
换算）；facts 未到的设备既不校正也不 hold——缺省动作是不动手。
"""

from __future__ import annotations

import asyncio
import time as time_module
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.contracts import HoldSource, JobOutcome
from app.core import actions, device_clock, outcomes, problems
from app.core.clock import format_rfc3339
from app.core.connections import manager
from app.core.logging import log_event
from app.core.problems import DEVICE_NOT_FOUND, VALIDATION_FAILED, ApiProblemError
from app.core.protocol import JsonObject
from app.db.session import session_scope
from app.modules.config import store as config_store
from app.modules.config.spec import HOLD_ENABLED_KEY, HOLD_STUDENTS_KEY, parse_time
from app.modules.devices import actions as device_actions
from app.modules.devices import crud as devices_crud
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.sessions import service as sessions_service
from app.modules.sessions.decision import students_of
from app.modules.sessions.schemas import AssignRequest
from app.persistence import audit as audit_store

from .schemas import DoorAssignment, HoldStatus

#: 对账间隔（秒）。触发窗（close − 9 分钟起、到 close 止）按 30 秒一轮有十余次机会。
TICK_INTERVAL_S = 30.0

#: hold 触发提前量（分钟）：浴室 App 自己的放人线是 close − 5 分钟，再留 4 分钟
#: 操作余量。是相对余量（策略），不是自定时刻。
HOLD_TRIGGER_MARGIN_MIN = 9

#: 偏置落点 = 午夜后这个余量（分钟）：跳过 App 的一天边界，它的 autoCloseTime 变成
#: 约 24 小时后，整夜不再触发「关机前 5 分钟放人」。偏移量按施加时刻的表盘现算——
#: 平台把 close 改到 00:30 之类的场景同样成立。
LANDING_MARGIN_MIN = 10

#: 租约续期间隔与租期（秒）：hold 期间每 2 分钟把 MCU 截止重布到"表盘此刻 + 租期"。
#: 服务端消失，最迟一个租期后断电（失效安全且有界）。
LEASE_RENEW_INTERVAL_S = 120.0
LEASE_TTL_S = 600.0

#: 校正阈值（秒）：设备钟晶振日漂移秒级，累积数日才到 60 秒——避免为小偏差频繁拨钟。
TIME_SYNC_THRESHOLD_S = 60

#: 同设备同事件的 WARNING 限频（秒）：对账每轮重试，失败日志不刷屏。
WARN_THROTTLE_S = 60.0

_tasks: set[asyncio.Task[None]] = set()
#: 每设备的节流标记（循环单调钟）：step 与租约续期在一次成功后不重复。进程级，
#: 重启后的首轮多做一次幂等写（目标相同，结果一致）——注释即承诺。
_last_step_at: dict[str, float] = {}
_last_arm_at: dict[str, float] = {}
_last_warn_at: dict[str, float] = {}

_running = False


def _record(outcome: str, message: str, device_id: str, *, level: str = "INFO", **extra: object) -> None:
    """电源计划的一次终局：日志与 job_runs 同一条路（outcome 即事件名后缀）。"""
    audit_store.record_job_run(
        event=f"power.{outcome}",
        message=message,
        level=level,
        device_id=device_id,
        door_no=0,
        cert_no="",
        job="power",
        outcome=JobOutcome(outcome),
        extra=extra,
    )


def _warn_once(device_id: str, key: str, event: str, message: str, **extra: object) -> None:
    """同一设备同一件事的 WARNING 限频（60 秒一条）：对账失败下一轮会重试，
    每轮一条会把真正的问题淹掉。"""
    now = time_module.monotonic()
    if now - _last_warn_at.get(f"{device_id}:{key}", 0.0) < WARN_THROTTLE_S:
        return
    _last_warn_at[f"{device_id}:{key}"] = now
    log_event("WARNING", event, message, device_id=device_id, **extra)


# ── 原语：全库仅有的两个钟面/布防入口 ──────────────────────────────────────


async def _step_wall_clock(session: Session, device_id: str, target_wall: str, *, rearm: bool) -> bool:
    """把设备墙钟一次性写到一个绝对钟面（chrony makestep 语义）。

    [target_wall] 是设备表盘上该显示的时刻（`YYYY-MM-DD HH:MM:SS`）。目标永远由
    服务端轴计算，与设备当前读数无关——重复写结果一致，误差不可能累积。
    [rearm] 为 true 时设备端写钟后按本地事实重布 MCU 常规计划，供恢复使用；
    hold 施加传 false（流程自己管理租约）。
    """
    result = await device_actions.send_command_and_wait(
        session, device_id, actions.CLOCK_SET, {"datetime": target_wall, "rearm": rearm}
    )
    if result.get("status") != outcomes.OK.value:
        _warn_once(
            device_id,
            f"step:{target_wall}",
            "power.step-failed",
            "墙钟写入失败",
            target=target_wall,
            reason_code=str(result.get("reason_code") or "-"),
        )
        return False
    _last_step_at[device_id] = time_module.monotonic()
    log_event("INFO", "power.step-applied", "设备墙钟已按绝对目标写准", device_id=device_id, target=target_wall)
    return True


async def _arm_mcu(session: Session, device_id: str, off_at: str, on_at: str) -> bool:
    """把一对断电/上电目标（设备表盘 HH:MM）写给 MCU；返回是否成功。

    设备端用自己的钟换算相对延时：断电延时从命令落地起算，上电延时从断电时刻起算，
    目标已过就落到次日（"下一个"）。一次布防覆盖上一次。
    """
    result = await device_actions.send_command_and_wait(
        session, device_id, actions.POWER_SCHEDULE, {"off_at": off_at, "on_at": on_at}
    )
    if result.get("status") != outcomes.OK.value:
        _warn_once(
            device_id,
            f"arm:{off_at}",
            "power.arm-failed",
            "MCU 布防失败",
            off_at=off_at,
            on_at=on_at,
            reason_code=str(result.get("reason_code") or "-"),
        )
        return False
    log_event("INFO", "power.armed", "MCU 已布防", device_id=device_id, off_at=off_at, on_at=on_at)
    return True


# ── 换算辅助 ──────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    """当前时刻（UTC，aware）。模块级函数而非散落的 datetime.now：测试注入时间
    的唯一入口（hold 施加/租约/恢复的判定都在它上面）。"""
    return datetime.now(UTC)


def _close_minute(close: str) -> int:
    """上报事实的 close（`HH:MM`）→ 当天分钟数。

    事实在网关入口已按 HH:MM 校验（device.status 分支），此处的非 None 是入口
    校验的推论；assert 只是把这条不变式写在明面上，不是兜底。"""
    minute = parse_time(close)
    assert minute is not None, f"close 形状损坏（网关入口应已拦截）：{close!r}"
    return minute


def _local_now(device_id: str, zone_offset_minutes: int) -> datetime:
    """设备表盘此刻的当地读数。调用方必须先用 device_clock.has 把门（无偏差记录即
    无从换算）；此处不再判空——门与换算分离，类型上就没有 None 可言。"""
    now_utc = _now_utc() - timedelta(seconds=device_clock.get(device_id))
    return now_utc.astimezone(timezone(timedelta(minutes=zone_offset_minutes)))


def _wall_text(moment_local: datetime) -> str:
    """表盘时刻（[to_local] 产出的 aware datetime）→ 设备墙钟文本。

    to_local 的本地字段就是表盘读数，直接取字段——不要经 format_rfc3339 绕回
    UTC 再格式化，那会把墙钟写成 UTC 字面（差一个时区；演绎阶段真实踩过）。
    """
    return moment_local.strftime("%Y-%m-%d %H:%M:%S")


def _landing_offset_s(local_now: datetime) -> int:
    """偏移量（秒）：从表盘此刻到"午夜后 [LANDING_MARGIN_MIN] 分钟"。

    对午夜前后的触发都成立：23:49 触发偏移 21 分钟落 00:10；00:05 触发（close=00:30）
    偏移 5 分钟同样落 00:10。
    """
    minute_of_day = local_now.hour * 60 + local_now.minute
    return ((24 * 60 - minute_of_day + LANDING_MARGIN_MIN) % (24 * 60)) * 60


def _hold_due(now_local: datetime, close_minute: int) -> bool:
    """触发窗：[close − 触发提前量, close)。close 早于提前量时窗口跨午夜
    （如 close=00:05 → 窗口 23:56–00:05），按模一天处理。"""
    trigger_minute = (close_minute - HOLD_TRIGGER_MARGIN_MIN) % (24 * 60)
    minute_of_day = now_local.hour * 60 + now_local.minute
    if trigger_minute <= close_minute:
        return trigger_minute <= minute_of_day < close_minute
    return minute_of_day >= trigger_minute or minute_of_day < close_minute


def _skipped_close_moment(hold_started_at: datetime, close_minute: int, zone_offset_minutes: int) -> datetime:
    """被跳过的断电时刻（真实当地轴）：hold 建立之后第一次到来的 autoCloseTime。

    恢复判定与意图过期都以它为界。day_offset=1 的候选必然晚于建立时刻，
    因此恒有返回值（不必 None 判空）。
    """
    started_local = hold_started_at.astimezone(timezone(timedelta(minutes=zone_offset_minutes)))
    # 当日候选若早于建立时刻（hold 建立当晚 close 已过），次日的同一时刻必然晚于它
    # ——日期严格后移一天。因此循环恒有返回，不需要兜底分支。
    for day_offset in (0, 1):
        moment = started_local.replace(
            hour=close_minute // 60, minute=close_minute % 60, second=0, microsecond=0
        ) + timedelta(days=day_offset)
        if moment > started_local:
            return moment
    return moment


# ── 对账器 ────────────────────────────────────────────────────────────────


async def tick_once() -> None:
    """对账入口：core/background 每 [TICK_INTERVAL_S] 调一轮。会话在本协程内开、内关。"""
    with session_scope() as session:
        devices = devices_service.list_devices(session)
        for record in devices:
            if record["retired"]:
                continue
            await _reconcile_device(session, str(record["device_id"]))


async def _reconcile_device(session: Session, device_id: str) -> None:
    """一台设备的对账：使影子行状态收敛于期望。每一步幂等，失败留痕等下一轮。"""
    facts = devices_service.power_facts(session, device_id)
    if facts is None:
        _warn_once(device_id, "facts", "power.facts-missing", "设备尚未上报开关机计划，本轮跳过")
        return
    close, _open_at, zone = facts
    config = config_store.effective(session, device_id)
    hold = devices_crud.get_hold(session, device_id)

    if hold is None:
        await _correct_clock(session, device_id, config, zone)
        if config[HOLD_ENABLED_KEY]:
            await _maybe_request_automated_hold(session, device_id, config, close, zone)
        hold = devices_crud.get_hold(session, device_id)
        if hold is None:
            return

    if hold["offset_s"] is None:
        await _apply_hold(session, device_id, config, hold, facts)
        return
    await _renew_lease(session, device_id, facts)
    await _resume_if_due(session, device_id, hold, facts)


async def _maybe_request_automated_hold(
    session: Session,
    device_id: str,
    config: JsonObject,
    close: str,
    zone: int,
) -> None:
    """名单里的学生还在占用，且到了触发窗、设备在线 → 建立 hold 意图（状态先行）。"""
    if not manager.is_online(device_id):
        _warn_once(device_id, "trigger-offline", "power.trigger-offline", "触发窗内设备离线，本轮不建立 hold")
        return
    if not device_clock.has(device_id):
        return  # 首次 pong 之前表盘算不出：触发窗无法判定
    local_now = _local_now(device_id, zone)
    if not _hold_due(local_now, _close_minute(close)):
        return
    students = students_of(config, HOLD_STUDENTS_KEY)
    occupying = [item for item in doors_service.open_sessions(session, device_id) if item.cert_no in students]
    if not occupying:
        return  # 没人在用，不必 hold
    devices_crud.start_hold(session, device_id, HoldSource.AUTOMATED)
    log_event(
        "INFO",
        "power.hold-requested",
        "名单内学生仍在占用，建立今晚 hold",
        device_id=device_id,
        students=len(occupying),
    )


async def _apply_hold(
    session: Session,
    device_id: str,
    config: JsonObject,
    hold: dict[str, Any],
    facts: tuple[str, str, int],
) -> None:
    """施加偏置。顺序硬约束：**布防先于拨钟**——墙钟被拨过旧布防的截止即当场断电
    （见 docs/device-poweroff.md），必须先把截止推到落点之后。"""
    close, open_at, zone = facts
    students = students_of(config, HOLD_STUDENTS_KEY)
    skipped_close = _skipped_close_moment(hold["started_at"], _close_minute(close), zone)
    now_real_local = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    if now_real_local > skipped_close:
        # close 已过（离线错过整窗、或意图拖到次日）：作废本轮 hold，不无意义拨钟
        devices_crud.clear_hold(session, device_id)
        _record("skipped", "close 已过，hold 意图作废", device_id, level="WARNING")
        return

    if not device_clock.has(device_id):
        _warn_once(device_id, "no-skew", "power.no-skew", "还没有钟偏差记录，暂缓施加")
        return
    local_now = _local_now(device_id, zone)
    offset_s = _landing_offset_s(local_now)
    assignments = [
        {"door_no": item.door_no, "cert_no": item.cert_no, "done": False, "failed": False}
        for item in doors_service.open_sessions(session, device_id)
        if item.cert_no in students
    ]

    # ① 预补偿布防：截止推到"落点 + 租期"之后，拨钟才不会越过旧截止当场断电
    deadline_wall = local_now + timedelta(seconds=offset_s + LEASE_TTL_S)
    if not await _arm_mcu(session, device_id, deadline_wall.strftime("%H:%M"), open_at):
        _record("hold-failed", "预补偿布防没成功，下一轮重试", device_id, level="WARNING")
        return
    # ② 写钟到落点（绝对钟面；目标与设备当前读数无关，重复写结果一致）
    if not await _step_wall_clock(session, device_id, _wall_text(local_now + timedelta(seconds=offset_s)), rearm=False):
        _record("hold-failed", "墙钟写入没成功，下一轮重试", device_id, level="WARNING")
        return
    # ③ 施加结果落库 + 审计（意图已在状态先行步落库）
    lease_deadline = _now_utc() + timedelta(seconds=LEASE_TTL_S)
    devices_crud.mark_hold_applied(session, device_id, offset_s, assignments, lease_deadline)
    _record("hold-applied", "今晚 hold 已施加", device_id, offset_s=offset_s, students=len(assignments))


async def _renew_lease(session: Session, device_id: str, facts: tuple[str, str, int]) -> None:
    """租约续期：hold 期间每 [LEASE_RENEW_INTERVAL_S] 把 MCU 截止重布到"表盘此刻 +
    租期"。服务端消失后最迟一个租期断电。"""
    last = _last_arm_at.get(device_id)
    if last is not None and time_module.monotonic() - last < LEASE_RENEW_INTERVAL_S:
        return
    if not device_clock.has(device_id):
        _warn_once(device_id, "no-skew", "power.no-skew", "还没有钟偏差记录，本轮无法续租")
        return
    _close, open_at, zone = facts
    local_now = _local_now(device_id, zone)
    deadline_wall = local_now + timedelta(seconds=LEASE_TTL_S)
    if await _arm_mcu(session, device_id, deadline_wall.strftime("%H:%M"), open_at):
        devices_crud.set_lease(session, device_id, _now_utc() + timedelta(seconds=LEASE_TTL_S))
        _last_arm_at[device_id] = time_module.monotonic()
        log_event(
            "INFO",
            "power.lease-renewed",
            "MCU 租约已续期",
            device_id=device_id,
            deadline=deadline_wall.strftime("%H:%M"),
        )


async def _resume_if_due(
    session: Session,
    device_id: str,
    hold: dict[str, Any],
    facts: tuple[str, str, int],
) -> None:
    """恢复判定：被跳过的 close 已过且设备空闲 → 写回真实钟面（rearm=true 一条命令
    同时完成写准与常规重布），清 hold 列，回到 following_schedule。"""
    close, _open_at, zone = facts
    skipped_close = _skipped_close_moment(hold["started_at"], _close_minute(close), zone)
    now_real_local = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    if now_real_local <= skipped_close:
        return
    if doors_service.open_sessions(session, device_id):
        return  # 有人在用：钟跳会清掉会话，等人走再恢复
    if not manager.is_online(device_id):
        return  # 设备已失联（租约到点即断电）：恢复等它回连、空闲后自动进行
    await resume(session, device_id)


# ── 校正（following_schedule 下才进行；hold 期间的偏差是故意的）────────────


async def _correct_clock(session: Session, device_id: str, config: JsonObject, zone: int) -> None:
    """自动校时：偏差超过阈值且无人占用时，把墙钟一次性写准（绝对钟面）。

    目标 = 真实当地钟面（服务端轴 + 上报时区）。rearm=true：设备端写准后按本地
    事实重布常规计划，节奏自愈。
    """
    if not config.get("time_sync_enabled"):
        return
    if not device_clock.has(device_id):
        return  # 还没有偏差记录（首次 pong 之前），无从判
    if doors_service.open_sessions(session, device_id):
        return  # 有人占用：校钟的跳变会清掉会话，等人走再校
    skew = device_clock.get(device_id)
    if abs(skew) < TIME_SYNC_THRESHOLD_S:
        return
    local_real = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    log_event("INFO", "time-sync.step-pending", "设备钟偏差超阈值，写准", device_id=device_id, skew_s=round(skew, 1))
    if await _step_wall_clock(session, device_id, _wall_text(local_real), rearm=True):
        log_event("INFO", "time-sync.corrected", "设备钟偏差已校正", device_id=device_id, skew_s=round(skew, 1))
    else:
        _warn_once(device_id, "correct", "time-sync.correction-failed", "设备钟偏差校正失败，下一轮重试")


# ── 放回（帧驱动）────────────────────────────────────────────────────────


def on_frame(device_id: str) -> None:
    """帧到达：把 hold 施加那一刻被清掉的名单学生放回原喷头（每人一次）。

    同步、立刻返回——帧处理路径不能等命令；真正的分配推到后台任务。放回标记在
    hold_assignments 的 JSON 里（done/failed），进程重启不丢。
    """
    if not _running:
        return
    try:
        task = asyncio.get_running_loop().create_task(_on_frame_task(device_id))
    except Exception as exc:  # noqa: BLE001 —— 调度失败只留痕，不拖垮帧回执
        log_event("ERROR", "power.schedule-failed", "放回任务调度失败", device_id=device_id, error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _on_frame_task(device_id: str) -> None:
    with session_scope() as session:
        hold = devices_crud.get_hold(session, device_id)
        if hold is None or hold["offset_s"] is None or not hold["assignments"]:
            return
        config = config_store.effective(session, device_id)
        students = students_of(config, HOLD_STUDENTS_KEY)
        occupying = {item.cert_no: item for item in doors_service.open_sessions(session, device_id)}
        await _reassign(session, device_id, hold, students, occupying)


async def _reassign(
    session: Session,
    device_id: str,
    hold: dict[str, Any],
    students: dict[str, str],
    occupying: dict[str, Any],
) -> None:
    """把名单里的人放回施加瞬间的原喷头——**每人一次**。

    - 施加后他自己重开了会话（assigned_at ≥ hold_started_at）→ 已成立，标记 done；
    - 施加前的残留会话还在（浴室端尚未清场）→ 等，不标记；
    - 前置拒（门被别人占着、他已有会话）→ 命令根本没下发，等下一帧，不算重试；
    - 命令真发出去而失败 → 标记 failed，不再试（每轮重发不会有不同结果）。
    """
    started_at = format_rfc3339(hold["started_at"])
    for index, assignment in enumerate(hold["assignments"]):
        if assignment.get("done") or assignment.get("failed"):
            continue
        door_no = int(assignment["door_no"])
        cert_no = str(assignment["cert_no"])
        password = students.get(cert_no)
        if password is None:
            log_event("INFO", "power.students-changed", "他不在名单里了，不分配", device_id=device_id)
            devices_crud.mark_assignment(session, device_id, index, done=True)
            continue
        door_session = occupying.get(cert_no)
        if door_session is not None and str(door_session.assigned_at) >= started_at:
            devices_crud.mark_assignment(session, device_id, index, done=True)  # 已成立
            continue
        if door_session is not None:
            continue  # 施加前的残留会话还没被清场：等
        try:
            result = await sessions_service.perform_assign(
                session, device_id, AssignRequest(door_no=door_no, cert_no=cert_no, password=password)
            )
        except ApiProblemError as exc:
            log_event(
                "INFO",
                "power.reassign-waiting",
                "还轮不到放回，等下一帧",
                device_id=device_id,
                door_no=door_no,
                problem=exc.problem.code,
            )
            continue
        ok = result.get("status") == outcomes.OK.value
        devices_crud.mark_assignment(session, device_id, index, done=ok)
        _record(
            "reassigned" if ok else "reassign-failed",
            "已放回原喷头" if ok else "放回没成功，这次不再替他试",
            device_id,
            level="INFO" if ok else "WARNING",
            door_no=door_no,
            cert_no=cert_no,
            reason_code=str(result.get("reason_code") or "-"),
        )


# ── 端点核心与看板 ────────────────────────────────────────────────────────


def request_hold(session: Session, device_id: str) -> HoldStatus:
    """今晚延长（manual hold）：建立意图，对账器在下一轮（≤30 秒）施加。

    已在 hold 中时幂等返回现状；主开关关闭时拒绝。不要求设备在线——意图持久，
    离线错过触发窗由对账器的作废守卫处理。
    """
    record = devices_service.get_device(session, device_id)
    if record is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    if record["retired"]:
        raise ApiProblemError(VALIDATION_FAILED, f"设备 {device_id} 已停用")
    config = config_store.effective(session, device_id)
    if not config.get(HOLD_ENABLED_KEY):
        raise ApiProblemError(VALIDATION_FAILED, "延长服务已停用")
    existing = devices_crud.get_hold(session, device_id)
    if existing is None:
        devices_crud.start_hold(session, device_id, HoldSource.MANUAL)
        log_event("INFO", "power.hold-requested", "运维手动建立今晚 hold", device_id=device_id)
        existing = devices_crud.get_hold(session, device_id)
    assert existing is not None  # start_hold 刚建立了它；设备不在册的早一步被守卫挡下
    return _hold_view(session, device_id, existing)


async def resume(session: Session, device_id: str) -> dict[str, object]:
    """恢复（Resume Schedule）：写回真实钟面并按本地事实重布常规计划。

    未在 hold 中时幂等返回 resumed=false；设备离线拒绝（写钟命令发不出去）。
    """
    if devices_service.get_device(session, device_id) is None:
        raise ApiProblemError(problems.DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    hold = devices_crud.get_hold(session, device_id)
    if hold is None:
        return {"device_id": device_id, "resumed": False}
    if not devices_service.is_online(device_id):
        raise ApiProblemError(problems.DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")
    facts = devices_service.power_facts(session, device_id)
    if facts is None:
        _warn_once(device_id, "resume-target", "power.resume-failed", "设备尚未上报开关机计划，恢复失败")
        return {"device_id": device_id, "resumed": False}
    zone = facts[2]
    local_real = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    ok = await _step_wall_clock(session, device_id, _wall_text(local_real), rearm=True)
    if ok:
        devices_crud.clear_hold(session, device_id)
        _record("resumed", "已恢复常规电源计划", device_id)
    else:
        _record("resume-failed", "恢复的墙钟写入没成功，下一轮对账重试", device_id, level="WARNING")
    return {"device_id": device_id, "resumed": bool(ok)}


def holds(session: Session) -> list[HoldStatus]:
    """看板：全部 hold 中的设备与恢复条件两项（close 已过 / 喷头空闲）。"""
    result: list[HoldStatus] = []
    for record in devices_service.list_devices(session):
        if record["retired"]:
            continue
        device_id = str(record["device_id"])
        hold = devices_crud.get_hold(session, device_id)
        if hold is None:
            continue
        result.append(_hold_view(session, device_id, hold))
    return result


def _hold_view(session: Session, device_id: str, hold: dict[str, Any]) -> HoldStatus:
    facts = devices_service.power_facts(session, device_id)
    past_close = False
    idle = not doors_service.open_sessions(session, device_id)
    if facts is not None and hold["offset_s"] is not None:
        close, _open_at, zone = facts
        skipped_close = _skipped_close_moment(hold["started_at"], _close_minute(close), zone)
        now_real_local = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
        past_close = now_real_local > skipped_close
    assignments = hold["assignments"] or []
    pending: list[DoorAssignment] = [
        {"door_no": int(item["door_no"]), "cert_no": str(item["cert_no"])}
        for item in assignments
        if not item.get("done") and not item.get("failed")
    ]
    return {
        "device_id": device_id,
        "hold_source": str(hold["source"].value) if hold["source"] else "",
        "hold_started_at": format_rfc3339(hold["started_at"]) if hold["started_at"] else "",
        "hold_offset_s": hold["offset_s"],
        "lease_deadline": format_rfc3339(hold["lease_deadline"]) if hold["lease_deadline"] else "",
        "total": len(assignments),
        "reassigned_count": sum(1 for item in assignments if item.get("done")),
        "pending": pending,
        "past_close": past_close,
        "idle": idle,
    }


# ── 生命周期 ──────────────────────────────────────────────────────────────


def start() -> None:
    global _running
    _running = True
    log_event("INFO", "power.started", "电源计划对账已启动")


async def stop() -> None:
    global _running
    _running = False
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
    _tasks.clear()
    _last_step_at.clear()
    _last_arm_at.clear()
    _last_warn_at.clear()
    log_event("INFO", "power.stopped", "电源计划对账已停止")


__all__ = [
    "LEASE_RENEW_INTERVAL_S",
    "LEASE_TTL_S",
    "TICK_INTERVAL_S",
    "holds",
    "on_frame",
    "request_hold",
    "resume",
    "start",
    "stop",
    "tick_once",
]
