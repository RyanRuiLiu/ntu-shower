"""电源计划域：上报事实、今晚延长（hold）与钟面校正的统一对账器。

## 行业原型

- **计划与例外**：BACnet Schedule——常规夜由设备上报的平台计划自持（MCU 每晚断电、
  清晨上电），hold 是一条只覆盖当夜的例外；
- **用户语义**：恒温器 Hold Until / Resume Schedule——hold 一次性，恢复自动回计划；
- **状态形状**：devices 影子行（AWS IoT Shadow 同型）——上报事实（reported）与
  hold 状态同 row，不另立表；
- **执行模型**：对账（Kubernetes controller）——每 tick 使实际收敛于期望，每一步
  幂等、崩溃重入安全；
- **钟写**：chrony makestep——所有写入都是绝对钟面的一次性 step（校正写准、hold
  施加写"真实+偏移"），永不增量拨。

## MCU 是钟面闹钟，hold 是一次布防

MCU 的 0xA1 布防把"距现在几小时几分钟"换算成**布防那刻钟面的一个 HH:MM 截止**，
此后钟面走到那个时刻即断电（docs/device-poweroff.md：拨过截止立即断电、
回拨等量推迟）。由此两条铁律：

1. **拨钟会吃掉钟面距离**。布防写在拨钟前（此刻墙钟=真实轴），拨钟把墙钟推前
   offset_s，截止必须再让出同样的量——少让一次，实际断电就提前一个 offset_s
   （2026-11-20 事故：六台在 apply+租期而非落点+租期集体断电）。
2. **滚动续写与钟面检查冲突**。每次续写的截止可能不晚于已在位的那个，写下去要么
   被忽略、要么把断电拉前。所以 hold 的布防**只有一次**：截止 = 落点 + 余量，
   此后不再下发任何布防。服务端消失，截止照常到点——那正是期望终态，无需
   另设失效安全窗口。

## 状态机

- **intent**：hold_started_at 落库、offset_s 为空。触发窗外 park（手动提前点击
  在此等待），close 已过作废，窗内施加。
- **applied**：一次布防（截止 = 落点 + 余量）+ 一次拨钟（到落点），started_at
  就位为施加时刻。余下只有两件事：提前掉线的可见性（布防可能被 MCU 静默丢弃）
  与落点恢复。
- **resumed**：落点已过且喷头空闲 → 清 hold 列。**不发命令、不动钟面**——钟面
  写回由 following_schedule 的自动校时承担（恢复后偏差 = 偏移量，超阈值即触发，
  其重布按营业时段判定，过 close 不重布，否则延长会被变成 24 小时）。落点前的
  手动 resume 是尽力取消：重布一个更早的截止（MCU 接受更早截止时生效，否则被
  忽略，断电仍落在落点 + 余量），随后清状态。

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
互斥是显式的（读 hold 列），不是隐式的域间约定。校正依赖上报的时区与 open/close
（目标钟面与重布判定都按它们换算）；facts 未到的设备既不校正也不 hold——缺省动作
是不动手。
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

#: 对账间隔（秒）。触发窗（close − 10 分钟起、到 close 止）按 30 秒一轮有约二十次机会。
TICK_INTERVAL_S = 30.0

#: hold 触发提前量（分钟）：浴室 App 自己的放人线是 close − 5 分钟，触发线再
#: 提前整整 5 分钟——预补偿布防与写钟两条命令在这段窗内从容完成。是相对余量
#: （策略），不是自定时刻。
HOLD_TRIGGER_MARGIN_MIN = 10

#: 偏置落点 = 午夜后这个余量（分钟）：跳过 App 的一天边界，它的 autoCloseTime 变成
#: 约 24 小时后，整夜不再触发「关机前 5 分钟放人」。偏移量按施加时刻的表盘现算——
#: 平台把 close 改到 00:30 之类的场景同样成立。
LANDING_MARGIN_MIN = 10

#: 布防余量（分钟）：MCU 截止 = 落点 + 这个余量。它同时是拨钟量的安全垫——截止
#: 必须严格晚于拨钟后的墙钟（否则拨钟瞬间断电），也要晚到"服务端消失也无关紧要"
#: （到点断电正是期望终态）。取消重布的截止（表盘此刻 + 1.5 分钟）落在它之内。
HOLD_MARGIN_MIN = 10

#: 校正阈值（秒）：设备钟晶振日漂移秒级，累积数日才到 60 秒——避免为小偏差频繁拨钟。
TIME_SYNC_THRESHOLD_S = 60

#: 同设备同事件的 WARNING 限频（秒）：对账每轮重试，失败日志不刷屏。
WARN_THROTTLE_S = 60.0

_tasks: set[asyncio.Task[None]] = set()
#: 同设备同事件的 WARNING 限频标记（循环单调钟）。进程级：重启后的首轮多做一次
#: 幂等留痕（内容相同），注释即承诺。
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


def _warn_once(device_id: str, key: str, event: str, message: str, **extra: object) -> bool:
    """同一设备同一件事的 WARNING 限频（60 秒一条）。返回是否真的发了——
    需要与日志同节奏落审计行的调用方据此判定。"""
    now = time_module.monotonic()
    if now - _last_warn_at.get(f"{device_id}:{key}", 0.0) < WARN_THROTTLE_S:
        return False
    _last_warn_at[f"{device_id}:{key}"] = now
    log_event("WARNING", event, message, device_id=device_id, **extra)
    return True


# ── 原语：全库仅有的两个钟面/布防入口 ──────────────────────────────────────


async def _step_wall_clock(session: Session, device_id: str, target_wall: str, *, rearm: bool) -> bool:
    """把设备墙钟一次性写到一个绝对钟面（chrony makestep 语义）。

    [target_wall] 是设备表盘上该显示的时刻（`YYYY-MM-DD HH:MM:SS`）。目标永远由
    服务端轴计算，与设备当前读数无关——重复写结果一致，误差不可能累积。
    [rearm] 由调用方按营业时段判定（见 [_correct_clock]）：设备端写钟后是否按本地
    事实重布 MCU 常规计划。hold 施加传 false（它自己的布防已经就位）。
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
    device_clock.forget(device_id)  # 旧偏差描述的是旧钟面：作废，等下一帧重新收敛
    log_event("INFO", "power.step-applied", "设备墙钟已按绝对目标写准", device_id=device_id, target=target_wall)
    return True


async def _arm_mcu(session: Session, device_id: str, off_at: str, on_at: str) -> bool:
    """把一对断电/上电目标（设备表盘 HH:MM）写给 MCU；返回是否成功。

    设备端用自己的钟换算相对延时：断电延时从命令落地起算，上电延时从断电时刻起算，
    目标已过就落到次日（"下一个"）。写入行为与重布语义见 docs/device-poweroff.md。
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
    的唯一入口（hold 施加/恢复的判定都在它上面）。"""
    return datetime.now(UTC)


def _fact_minute(text: str) -> int:
    """上报事实的时刻（`HH:MM`，close/open 都用）→ 当天分钟数。

    事实在网关入口已按 HH:MM 校验（device.status 分支），此处的非 None 是入口
    校验的推论；assert 只是把这条不变式写在明面上，不是兜底。"""
    minute = parse_time(text)
    assert minute is not None, f"事实时刻形状损坏（网关入口应已拦截）：{text!r}"
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

    意图过期以它为界。day_offset=1 的候选必然晚于建立时刻，因此恒有返回值
    （不必 None 判空）。
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
        await _correct_clock(session, device_id, config, facts)
        if config[HOLD_ENABLED_KEY]:
            await _maybe_request_automated_hold(session, device_id, config, close, zone)
        hold = devices_crud.get_hold(session, device_id)
        if hold is None:
            return

    if hold["offset_s"] is None:
        await _apply_hold(session, device_id, config, hold, facts)
        return
    _watch_armed(device_id, hold)
    await _resume_if_due(session, device_id, hold)


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
    if not _hold_due(local_now, _fact_minute(close)):
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
    """施加偏置：一次布防（截止 = 落点 + 余量）+ 一次拨钟（到落点）。

    顺序硬约束：**布防先于拨钟**——墙钟被拨过旧布防的截止即当场断电
    （docs/device-poweroff.md 的实测记录），必须先把截止推到拨后钟面之外。
    """
    close, open_at, zone = facts
    students = students_of(config, HOLD_STUDENTS_KEY)
    skipped_close = _skipped_close_moment(hold["started_at"], _fact_minute(close), zone)
    now_real_local = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    if now_real_local > skipped_close:
        # close 已过（离线错过整窗、或意图拖到次日）：作废本轮 hold，不无意义拨钟
        devices_crud.clear_hold(session, device_id)
        _record("skipped", "close 已过，hold 意图作废", device_id, level="WARNING")
        return
    if not _hold_due(now_real_local, _fact_minute(close)):
        return  # 触发窗未到：意图保持到窗内再施加（手动提前点击在此等待）

    if not manager.is_online(device_id):
        return  # 离线发不出命令：不发、不落审计行，上线后下一轮自动施加
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

    # ① 预补偿布防：截止 = 表盘此刻 + 2×偏移 + 余量。布防写在拨钟前（此刻墙钟=
    # 真实轴），拨钟把墙钟推前 offset_s——截止必须再让出同样的量，实际断电才落在
    # 落点 + 余量（真实轴）。少让一次就是 2026-11-20 的六台集体提前断电。
    deadline_wall = local_now + timedelta(seconds=2 * offset_s + HOLD_MARGIN_MIN * 60)
    if not await _arm_mcu(session, device_id, deadline_wall.strftime("%H:%M"), open_at):
        _record("hold-failed", "预补偿布防没成功，下一轮重试", device_id, level="WARNING")
        return
    # ② 写钟到落点（绝对钟面；目标与设备当前读数无关，重复写结果一致）
    if not await _step_wall_clock(session, device_id, _wall_text(local_now + timedelta(seconds=offset_s)), rearm=False):
        _record("hold-failed", "墙钟写入没成功，下一轮重试", device_id, level="WARNING")
        return
    # ③ 施加结果落库 + 审计（意图已在状态先行步落库）。started_at 就位为施加时刻：
    # 落点 = 施加时刻 + 偏移，恢复判定与看板都按这条轴算。
    devices_crud.mark_hold_applied(
        session,
        device_id,
        offset_s,
        assignments,
        _now_utc() + timedelta(seconds=offset_s + HOLD_MARGIN_MIN * 60),
    )
    _record("hold-applied", "今晚 hold 已施加", device_id, offset_s=offset_s, students=len(assignments))


def _watch_armed(device_id: str, hold: dict[str, Any]) -> None:
    """已施加的 hold：提前掉线的可见性。

    布防只有一次，MCU 又可能静默丢弃（docs/device-poweroff.md 的实测记录）——设备在落点
    前掉线意味着"延长可能没生效"，这一条必须有人看得见。掉线本身不触发任何补救
    （人不在就是断电，方向安全）；落点后掉线是断电的正常表现，不告警。
    """
    deadline = hold["lease_deadline"]
    if deadline is None or _now_utc() >= deadline:
        return
    if manager.is_online(device_id):
        return
    if _warn_once(device_id, "hold-offline", "power.hold-offline", "延长期间设备掉线，布防可能未生效"):
        _record("hold-failed", "延长期间设备掉线，请人工确认今晚是否延长", device_id, level="WARNING")


async def _resume_if_due(session: Session, device_id: str, hold: dict[str, Any]) -> None:
    """恢复判定：落点已过（施加时刻 + 偏移）且设备空闲 → 清 hold 列。

    恢复等**落点**，不等被跳过的 close——延长的全部意义就是越过 close，close 一过
    就恢复等于没延长（2026-11-20 事故：六台在 23:58 被恢复，午夜前后集体断电）。
    恢复本身不动钟面、不发命令：钟面写回由 following_schedule 的自动校时承担
    （恢复后偏差 = 偏移量，超阈值即触发）；今晚的断电由布防截止承担。
    """
    started_at = hold["started_at"]
    offset_s = hold["offset_s"]
    if started_at is None or offset_s is None:
        return
    if _now_utc() < started_at + timedelta(seconds=offset_s):
        return
    if doors_service.open_sessions(session, device_id):
        return  # 有人在用：钟跳会清掉会话，等人走再恢复
    if not manager.is_online(device_id):
        return  # 设备已失联（截止到点即断电）：恢复等它回连、空闲后自动进行
    await resume(session, device_id)


# ── 校正（following_schedule 下才进行；hold 期间的偏差是故意的）────────────


async def _correct_clock(session: Session, device_id: str, config: JsonObject, facts: tuple[str, str, int]) -> None:
    """自动校时：偏差超过阈值且无人占用时，把墙钟一次性写准（绝对钟面）。

    目标 = 真实当地钟面（服务端轴 + 上报时区）。营业时段内（open→close）写准后
    重布常规计划（rearm=true）；已过今晚 close 的校正不重布——此刻的重布把断电
    推到次日，延长的服务会被变成 24 小时，断电由既有布防截止承担。
    """
    if not config.get("time_sync_enabled"):
        return
    if not device_clock.has(device_id):
        return  # 还没有偏差记录（首次 pong 之前），无从判
    if doors_service.open_sessions(session, device_id):
        return  # 有人占用：校钟的跳变会清掉会话，等人走再校
    if not manager.is_online(device_id):
        return  # 夜间断电是常态：离线不发命令、不落审计行
    skew = device_clock.get(device_id)
    if abs(skew) < TIME_SYNC_THRESHOLD_S:
        return
    close, open_at, zone = facts
    close_minute = _fact_minute(close)
    open_minute = _fact_minute(open_at)
    local_real = _now_utc().astimezone(timezone(timedelta(minutes=zone)))
    in_open_hours = ((local_real.hour * 60 + local_real.minute - open_minute) % 1440) < (
        (close_minute - open_minute) % 1440
    )
    log_event("INFO", "time-sync.step-pending", "设备钟偏差超阈值，写准", device_id=device_id, skew_s=round(skew, 1))
    if await _step_wall_clock(session, device_id, _wall_text(local_real), rearm=in_open_hours):
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
    """今晚延长（manual hold）：建立意图，对账器在触发窗内施加。

    已在 hold 中时幂等返回现状；主开关关闭时拒绝。不要求设备在线——意图持久，
    离线错过触发窗由对账器的作废守卫处理；触发窗前的点击在对账器里 park 到窗内。
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
    """恢复（Resume Schedule）：清 hold 列，回到 following_schedule。

    不动钟面。落点已过（或尚未施加）时这是纯状态操作；落点前且已施加时它同时是
    **尽力取消**——重布一个更早的截止（表盘此刻 + 1.5 分钟），MCU 接受更早截止时
    生效，否则被忽略、断电仍落在落点 + 余量。无论哪种，都对"已布防截止"的任何
    重布保持警惕：把它推到次日的重布会把延长变成 24 小时（2026-11-20 事故里
    恢复后的按事实重布算出 0 分钟延时，六台在午夜集体断电）。
    未在 hold 中时幂等返回 resumed=false。
    """
    if devices_service.get_device(session, device_id) is None:
        raise ApiProblemError(problems.DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    hold = devices_crud.get_hold(session, device_id)
    if hold is None:
        return {"device_id": device_id, "resumed": False}
    await _cancel_arming_if_pending(session, device_id, hold)
    devices_crud.clear_hold(session, device_id)
    _record("resumed", "已恢复常规电源计划", device_id)
    return {"device_id": device_id, "resumed": True}


async def _cancel_arming_if_pending(session: Session, device_id: str, hold: dict[str, Any]) -> None:
    """落点前的尽力取消：重布一个更早的截止。

    只在布防已在位、落点未到、设备在线且偏差可换算时尝试；任何前提不满足就跳过
    （清状态照常，断电由既有截止承担）。截止取"表盘此刻 + 1.5 分钟"——比此刻晚、
    比落点早，且不与同分钟的 0 延时撞车。
    """
    if hold["offset_s"] is None or hold["started_at"] is None:
        return
    if _now_utc() >= hold["started_at"] + timedelta(seconds=hold["offset_s"]):
        return  # 落点已过：断电本来就到点了
    if not manager.is_online(device_id) or not device_clock.has(device_id):
        return
    facts = devices_service.power_facts(session, device_id)
    if facts is None:
        return
    local_now = _local_now(device_id, facts[2])
    await _arm_mcu(session, device_id, (local_now + timedelta(seconds=90)).strftime("%H:%M"), facts[1])


def holds(session: Session) -> list[HoldStatus]:
    """看板：全部 hold 中的设备与恢复条件两项（落点已过 / 喷头空闲）。"""
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
    past_close = False
    idle = not doors_service.open_sessions(session, device_id)
    if hold["offset_s"] is not None and hold["started_at"] is not None:
        # 落点已过 = 恢复条件之一（另一项是喷头空闲）；字段名沿用契约的 past_close
        past_close = _now_utc() >= hold["started_at"] + timedelta(seconds=hold["offset_s"])
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
    _last_warn_at.clear()


__all__ = [
    "HOLD_MARGIN_MIN",
    "HOLD_TRIGGER_MARGIN_MIN",
    "LANDING_MARGIN_MIN",
    "TICK_INTERVAL_S",
    "holds",
    "on_frame",
    "request_hold",
    "resume",
    "start",
    "stop",
    "tick_once",
]
