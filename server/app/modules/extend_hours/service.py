"""延长服务时间：到点把设备钟拨走，让人在规定时间之外还能用。

## 它做的事

浴室 App 每天按 `autoCloseTime`（实测 23:58）关机，并在关机前五分钟放掉所有喷头
（`architecture.md` 那四个释放条件里的"关机前 5 分钟"）。对名单里的人来说那一下是
提前收场。这里在更早的某一刻（`extend_hours_at`，默认 23:49）：

- 拨钟之前先重布一次 MCU（滚动重布的第一次，预补偿拨走的一小时）：钟被拨到越过
  旧布防的截止即当场断电（docs/device-poweroff.md），截止必须先推走；
- 把设备钟往后拨一小时，浴室 App 的释放条件全用绝对值（`|openTime − now|`），钟一跳
  它们统统越界——**设备上所有喷头被清空**，不只是名单里的那几个；
- 拨完之后等门清空，再替名单里的人把喷头分配回去。

## 滚动重布：为什么截止要反复重写，以及为什么是每 2 分钟

MCU 的断电截止是**钟面时刻**——钟被拨动，截止就在真实时间里跟着平移；而延长的时长
本质上由"名单里的人什么时候洗完"决定，**没有天然上界**。任何一次性的固定截止（无论
写多远）都迟早落到"有人还在用水"的时刻上——那一下就是打断。

所以延长期间 MCU 里的截止不是一条写死的计划，而是**滚动窗口**：武装的截止永远是
"表盘此刻 + `ROLL_WINDOW_MIN` 分钟"，每 `ROLL_REARM_INTERVAL_S`（2 分钟）续写一次。
只要服务端在续写，截止永远追不上钟，任何人都不会被打断；服务端一旦消失（宕机、断
联），最迟一个窗口（10 分钟）后断电——失效安全且有界。上电目标始终是次日
`power_on_at`，即使服务端消失，次日照常来电。

## 恢复条件：跳过的断电时刻已过，且设备上无人占用

拨回（把钟调回真实时刻）会让钟跳变一次，跳变会清掉当时还挂着的会话。所以拨回必须等：

- **被跳过的那次断电时刻已经过去**（`power_off_at` 在拨钟当晚的那一次，真实时刻）；
- **设备上没有任何未结束的会话**——预约着的与占用的都算，他们在就晚点拨回，
  等会话结束；人走了（会话结束）就立刻拨回，钟面失真的窗口越小越好。

拨回之后立即在新框架里重布一次（断电 = `power_off_at`、上电 = `power_on_at`）——当晚
的断电与次日的上电都由它承担，节奏与未延长的夜晚完全一致。

## 动作顺序

```
① 滚动重布（预补偿）  power.schedule  —— 截止推过"拨快的一刻钟 + 窗口"，拨快才不会当场断电
② 拨钟                clock.set(+15分) —— 浴室 App 还在跑，它下一轮扫描就会清空喷头
③ 重新分配            session.assign  —— 帧与定时任务推动，把名单里的人放回原喷头（每人一次）
④ 滚动续写            power.schedule  —— 每 2 分钟续写一次，延长期间无人被打断
⑤ 拨回 + 重布         clock.set(-15分) + power.schedule —— 无人占用后执行，节奏恢复
```

## 两个入口，各管一段

- [on_frame]：普通帧到达时调，推**重新分配**那一步。拨钟会让浴室 App 清空所有喷头，
  而清空必然写库、必然出一帧，所以"等门空"不需要轮询。
- [tick_once]：定时任务每半分钟调一次。它负责**拨钟**、**滚动续写**、**拨回**与
  **自动校时**（`_maybe_correct_clock`：延长之外把超阈值的钟偏差写准）——那几件
  到点时没有帧可等（帧是事件驱动的，状态不变就不报），只能自己看表。它还顺带推一次
  重新分配：帧平均四分钟一份，而那一步等不起。

## 为什么不与另外三个后台任务共用调度器

`sessions/scheduler` 的候选是**每个喷头一个**（`FEATURES` 那张表的 `states` / `start`
全是喷头级），而这里问的是"**整台设备**上还有没有名单里的人"。粒度不同，硬塞进去要在
那张表里放一个不成立的形状。另外三条还有一个更实在的差别：它们的记忆在内存里，丢了
只是少续一次；这一个丢了设备钟会一直偏着，所以状态落库（见 `crud.py`）。

## 与另外三条的边界只有一处

拨钟会把它们的会话清掉。那是既成事实，不是接口——浴室 App 自己清，我们没有调用它们、
它们也不需要知道我们。下一帧它们看到的只是"人没了"。
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.contracts import DeviceRecord
from app.core import actions, device_clock, outcomes
from app.core.clock import now_rfc3339, parse_rfc3339, shift_rfc3339, to_local, wall_clock_text
from app.core.logging import log_event
from app.core.problems import ApiProblemError
from app.core.protocol import as_int
from app.db.base import utcnow
from app.db.session import session_scope
from app.modules.config import store as config_store
from app.modules.config.schemas import ConfigValues
from app.modules.config.spec import (
    EXTEND_HOURS_ENABLED_KEY,
    EXTEND_HOURS_STUDENTS_KEY,
    POWER_OFF_AT_KEY,
    POWER_ON_AT_KEY,
    SERVER_ZONE_OFFSET_MINUTES_KEY,
    TIME_SYNC_ENABLED_KEY,
    format_time,
    parse_time,
)
from app.modules.devices import actions as device_actions
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.sessions import service as sessions_service
from app.modules.sessions.decision import students_of
from app.modules.sessions.schemas import AssignRequest
from app.persistence import audit as audit_store

from . import crud

#: 拨钟拨走的时长（秒）。落点必须跨过午夜，才能一次跳过 23:50 起的新人关闭窗口、
#: 23:58 的断电，以及两者之间的全部释放判定。曾是配置键（运维从未改过，
#: 2026-11-17 精简收编为常量）。
EXTEND_SHIFT_MINUTES = 15


def _shift_s(_config: ConfigValues) -> int:
    return EXTEND_SHIFT_MINUTES * 60


def _extend_hours_at(config: ConfigValues) -> str:
    """拨钟时刻：断电时刻前移固定余量。断电时刻的单一出处是 power_off_at。"""
    power_off = str(config[POWER_OFF_AT_KEY])
    minutes = parse_time(power_off)
    if minutes is None:
        return power_off
    shifted = minutes - EXTEND_HOURS_BEFORE_POWER_OFF_MIN
    return format_time(max(shifted, 0))


#: 拨钟时刻 = 断电时刻 − 这个余量（分钟）。断电时刻的唯一出处是 power_off_at 配置键
#: （浴室 App autoCloseTime 同值），拨钟必须落在它之前、且在"关机前 5 分钟放人线"
#: （23:53 = 23:58 − 5）之前再留出操作余量——23:58 − 9 = 23:49。曾是独立配置键
#: `extend_hours_at`（运维从未改过，2026-11-17 改为派生：改断电时刻拨钟自动跟）。
EXTEND_HOURS_BEFORE_POWER_OFF_MIN = 9

#: 自动校时的偏差阈值（秒）：设备钟晶振日漂移秒级，累积数日才到 60 秒——避免为小
#: 偏差频繁拨钟。曾是配置键（运维从未改过，2026-11-17 精简收编为常量）。
TIME_SYNC_THRESHOLD_S = 60

#: 扫表间隔。它要远小于"到点"与"浴室 App 放人"之间那几分钟余量（拨钟默认 23:49，
#: 放人线 23:53）。
TICK_INTERVAL_S = 30.0

#: 滚动续写间隔。窗口 10 分钟、续写 2 分钟意味着截止永远领先至少 8 分钟；续写失败
#: 下一轮 tick（30 秒）就重试，不需要等满间隔。命令量由此降为窗口方案的 1/4，
#: 失效安全上界不变（服务端消失最迟一个窗口后断电）。
ROLL_REARM_INTERVAL_S = 120.0

#: 滚动重布的窗口（分钟），也是失效安全的上界：服务端消失（宕机、断联）后，MCU 最迟
#: 一个窗口后按最后一次布防断电，上电目标始终是次日 `power_on_at`。窗口必须远大于
#: 续写间隔（120 秒）与命令延迟（p50 0.6 秒），10 分钟即"连续丢 5 次续写才到点"。
ROLL_WINDOW_MIN = 10


#: 设备 → 最近一次成功滚动续写的时刻（单调钟）与断电目标（表盘 HH:MM）。前者做
#: 限频（武装未满间隔不重复下发），后者给看板显示"MCU 里此刻武装的截止"；进程
#: 重启后两者为空，下一轮 tick（30 秒内）重新武装时自动填上。
_last_roll_at: dict[str, float] = {}
_last_roll: dict[str, str] = {}

#: (设备, 学号) → 这次延长里我们已经替他放回过、或确认他在拨钟之后自己有了会话。
#:
#: 放回是**一次性**的（见 `_reassign` 的注释）：这一次结束之后，浴室端的 300 秒无水
#: 门槛收回他就是"他走了"的信号，不再重新预约——否则拨回条件（喷头全部空闲）永远
#: 不成立，延长永远不结束。记忆在内存里，与 `_attempted` 同类：服务端重启就重来
#: 一轮，代价只是可能多放回一次，而窗口由库里的 `shifted_at` 算，不受它影响。
_reassigned: set[tuple[str, str]] = set()

#: (设备, 学号) → 这次延长里我们已经替他分配过、而命令失败了。
#:
#: 它与"前置拒"分得开：前置拒（门还占着）的命令**根本没下发**，下次还要试；命令真发出去
#: 而失败了就是失败，不再试——否则同一个人每轮都发一遍同样的命令，永远发不成功。
#: 记忆在内存里，与 `sessions/scheduler` 的 `_attempted` 同类：服务端重启就重来一轮，
#: 代价只是多一条 WARNING，而窗口由库里的 `shifted_at` 算，不受它影响。
_reassign_failed: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class Shift:
    """一次拨钟：哪台设备、什么时候拨成功、拨之前谁在哪个喷头。"""

    device_id: str
    shifted_at: str
    assignments: tuple[tuple[int, str], ...]


# ── 终局记录：日志与审计表同一条路 ────────────────────────────────────────


def _record(outcome: str, message: str, device_id: str, *, level: str = "INFO", **extra: object) -> None:
    """延长服务的一次终局：委托 audit 的唯一写路径（日志与审计表同一条路）。

    `outcome` 取日志事件名后缀（`extend-hours.<它>`），与 JobOutcome 的延长服务
    取值同一套说法；运维看板（/jobs/status 与 /audit/job-runs）据此一处看全
    四个后台任务。`extra` 只进日志（给人看的细节）。
    """
    audit_store.record_job_run(
        event=f"extend-hours.{outcome}",
        message=message,
        level=level,
        device_id=device_id,
        job="extend_hours",
        outcome=outcome,
        extra=extra,
    )


# ── 取数：一律经别人的公开功能 ──────────────────────────────────────────────


def _device_now(device_id: str) -> str | None:
    """设备钟此刻的读数（服务端轴上的写法）；这台设备还没有偏差记录时给 None。

    偏差的符号是「服务端 − 设备」（`core/device_clock` 的约定），所以设备钟 = 服务端
    此刻 − 偏差。**没有记录时必须给 None 而不是拿 0 顶上**：那等于假定这台没有 NTP 的
    设备与我们对齐，而偏差为零纯属巧合。
    """
    if not device_clock.has(device_id):
        return None
    return shift_rfc3339(now_rfc3339(), -device_clock.get(device_id))


def _local_now(device_id: str, zone_offset_minutes: int) -> str | None:
    """设备**表盘上**现在几点；无从确定时给 None。

    浴室 App 判时间用的是设备默认时区（它读 `Calendar.getInstance()`），所以表盘时刻
    = 设备钟 + 时区偏移。这两个量都取自设备那一侧，配错时区只会让"23:49"落在真实世界
    的另一个点上，而它与浴室 App 的相对关系仍然正确。
    """
    device_now = _device_now(device_id)
    if device_now is None:
        return None
    return shift_rfc3339(device_now, zone_offset_minutes * 60)


def _present(session: Session, device_id: str, students: dict[str, str]) -> list[tuple[int, str]]:
    """这台设备上此刻还占着喷头的名单里的人：(喷头号, 学号)。

    用**未结束的会话**当"占用"，而不是读喷头现态：占着的正是会话，而"重新分配"要的
    也是会话的主人。两者在正常路径上一致，而会话是更贴切的那个。
    """
    return [
        (item.door_no, item.cert_no)
        for item in doors_service.open_sessions(session, device_id)
        if item.cert_no in students
    ]


def _as_shift(row: dict[str, Any]) -> Shift:
    return Shift(
        device_id=str(row["device_id"]),
        shifted_at=str(row["shifted_at"]),
        assignments=tuple(
            (as_int(item.get("door_no")), str(item.get("cert_no", "")))
            for item in row["assignments"]
            if isinstance(item, dict)
        ),
    )


def _hhmm_minutes(text: str) -> int | None:
    """`HH:MM` → 自 0 点起的分钟数；格式不符给 None。"""
    moment = parse_rfc3339(f"2000-01-01T{text}:00.000Z")
    if moment is None:
        return None
    return moment.hour * 60 + moment.minute


def current_shifts(session: Session) -> list[dict[str, object]]:
    """正在延长中的设备：拨快幅度、拨快时刻、MCU 里武装的截止、恢复条件与放回进度。

    只读的运维可见性（概览页的后台任务卡）：表里一行就是"这台设备钟是拨快着的"。
    恢复条件两项（真实时间过午夜、喷头全部空闲）逐项给出——面板能直接回答
    "什么时候拨回"：两项齐了之后的一轮 tick（30 秒内）就会动手。`pending` 是
    尚未放回的人：浴室端收回谁，谁就是走了，不再重新预约。
    """
    rows: list[dict[str, object]] = []
    for row in crud.list_shifted(session):
        shift = _as_shift(row)
        config = config_store.effective(session, shift.device_id)
        zone = as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])
        shift_local = to_local(shift.shifted_at, zone)
        now_local = to_local(now_rfc3339(), zone)
        past_midnight = shift_local is not None and now_local is not None and now_local.date() > shift_local.date()
        idle = not doors_service.open_sessions(session, shift.device_id)
        pending = [
            {"door_no": door_no, "cert_no": cert_no}
            for door_no, cert_no in shift.assignments
            if (shift.device_id, cert_no) not in _reassigned
        ]
        rows.append(
            {
                "device_id": shift.device_id,
                "shift_minutes": EXTEND_SHIFT_MINUTES,
                "shifted_at": shift.shifted_at,
                "total": len(shift.assignments),
                "reassigned_count": len(shift.assignments) - len(pending),
                "pending": pending,
                "mcu_off_at": _last_roll.get(shift.device_id, ""),
                "rearm_interval_s": int(ROLL_REARM_INTERVAL_S),
                "idle": idle,
                "past_midnight": past_midnight,
            }
        )
    return rows


# ── 动作 ──────────────────────────────────────────────────────────────────


async def _set_clock(session: Session, device_id: str, seconds: float, zone_offset_minutes: int) -> bool:
    """把设备钟平移 seconds 秒；返回是否成功。

    目标时刻要**先加时区偏移**再交给 `wall_clock_text`：设备端按自己的默认时区解析那个
    字符串，所以它要收的是"表盘上该显示几点"，不是 UTC 字面。少了这一步，钟会拨到比
    目标早或晚一个时区的地方（UTC+8 上就是早八小时）。
    """
    device_now = _device_now(device_id)
    if device_now is None:
        log_event("WARNING", "extend-hours.no-skew", "还没有这台设备的钟偏差记录，不动它", device_id=device_id)
        return False
    target = shift_rfc3339(device_now, zone_offset_minutes * 60 + seconds)
    text = wall_clock_text(target) if target is not None else None
    if text is None:
        log_event("WARNING", "extend-hours.bad-target", "算不出目标时刻，不动它", device_id=device_id)
        return False

    # rearm=false：延长流程自己管理 MCU 的滚动重布，拨快/拨回不触发设备端的配对重布
    written = await device_actions.send_command_and_wait(
        session, device_id, actions.CLOCK_SET, {"datetime": text, "rearm": False}
    )
    if written["status"] != outcomes.OK.value:
        log_event(
            "WARNING",
            "extend-hours.clock-failed",
            "写时钟失败",
            device_id=device_id,
            target=text,
            reason_code=written["reason_code"] or "-",
        )
        return False

    _record("clock-moved", "设备钟已平移", device_id, seconds=int(seconds), target=text)
    # 钟是我们拨的，偏差记录当场跟上：等服务端自发发现要等下一次 pong，那 30 秒里按
    # 表盘换算的目标（同轮的滚动续写）都带着过时的偏差。
    devices_service.shift_clock_skew(session, device_id, seconds)
    return True


async def _schedule_power(session: Session, device_id: str, off_at: str, on_at: str) -> bool:
    """把一对断电/上电目标（设备表盘 HH:MM）写给 MCU；返回是否成功。

    设备端用自己的钟换算相对延时：断电延时从命令落地起算，开机延时从断电时刻起算，
    目标已过就落到次日（"下一个"）。见 [McuPowerSchedule]。
    """
    result = await device_actions.send_command_and_wait(
        session, device_id, actions.POWER_SCHEDULE, {"off_at": off_at, "on_at": on_at}
    )
    if result["status"] != outcomes.OK.value:
        log_event(
            "WARNING",
            "extend-hours.power-failed",
            "重布 MCU 定时开关机失败",
            device_id=device_id,
            off_at=off_at,
            on_at=on_at,
            reason_code=result["reason_code"] or "-",
        )
        return False
    log_event(
        "INFO",
        "extend-hours.power-scheduled",
        "已重布 MCU 定时开关机",
        device_id=device_id,
        off_at=off_at,
        on_at=on_at,
    )
    return True


def _roll_target(device_id: str, config: ConfigValues, pre_shift: bool) -> tuple[str, str] | None:
    """滚动重布的一对目标（断电 HH:MM，上电 HH:MM）；算不出给 None。

    断电目标 = 表盘此刻 + 窗口；拨钟之前的那一次要**预补偿拨走的一小时**——拨快会让
    钟越过未补偿的截止，当场断电（实测）。上电目标恒为 `power_on_at`（设备端按
    "断电时刻之后第一个上电时刻"换算）。
    """
    local_now = _local_now(device_id, as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY]))
    if local_now is None:
        return None
    shift_comp = _shift_s(config) if pre_shift else 0
    off_wall = wall_clock_text(shift_rfc3339(local_now, shift_comp + ROLL_WINDOW_MIN * 60))
    if off_wall is None:
        return None
    return off_wall[11:16], str(config[POWER_ON_AT_KEY])


async def _roll_power(session: Session, device_id: str, config: ConfigValues, pre_shift: bool) -> bool:
    """滚动重布一次；返回是否成功。

    设备离线时命令失败如实回报——最迟一个窗口后按最后一次布防断电，上电目标仍是次日
    `power_on_at`，失效安全。成功的那一笔断电目标记进 `_last_roll`，看板据此显示
    "MCU 里此刻武装的截止"。
    """
    targets = _roll_target(device_id, config, pre_shift)
    if targets is None:
        log_event("WARNING", "extend-hours.no-skew", "还没有这台设备的钟偏差记录，不动它", device_id=device_id)
        return False
    off_at, on_at = targets
    if await _schedule_power(session, device_id, off_at, on_at):
        _last_roll[device_id] = off_at
        _last_roll_at[device_id] = time.monotonic()
        return True
    return False


async def _maybe_correct_clock(session: Session, device_id: str, config: ConfigValues) -> None:
    """自动校时（`time_sync_enabled`）：偏差超过阈值就把设备钟写准。

    设备没有 NTP，整夜不重启（延长服务就是这种场景）钟也会漂，手工拨动更会留下大偏差。
    偏差超过阈值时服务端主动下发 clock.set，设备端校钟后按电源计划重布 MCU，节奏自愈。
    校正的跳变与拨回一样会清掉当时挂着的会话，所以三种情形让位：**延长中不校**（偏差是
    故意的，拨快本来就是流程的一部分）、**有人在占用不校**（判定与 `_restore_due` 同源）、
    **还没有偏差记录不校**（首次 pong 之前无从判）。
    """
    if not config[TIME_SYNC_ENABLED_KEY]:
        return
    if crud.get(session, device_id) is not None:
        return  # 延长中：偏差是故意的
    if not device_clock.has(device_id):
        return  # 还没有偏差记录（首次 pong 之前），无从判
    if doors_service.open_sessions(session, device_id):
        return  # 有人占用：校钟的跳变会清掉会话，等人走再校
    skew = device_clock.get(device_id)  # 服务端 − 设备（秒）
    threshold = TIME_SYNC_THRESHOLD_S
    if abs(skew) < threshold:
        return
    zone = as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])
    text = wall_clock_text(shift_rfc3339(now_rfc3339(), zone * 60))
    if text is None:
        return
    result = await device_actions.send_command_and_wait(
        session, device_id, actions.CLOCK_SET, {"datetime": text, "rearm": True}
    )
    if result["status"] != outcomes.OK.value:
        log_event(
            "WARNING",
            "time-sync.correction-failed",
            "运行时钟偏差校正失败",
            device_id=device_id,
            skew_s=round(skew, 1),
            reason_code=result["reason_code"] or "-",
        )
        return
    log_event(
        "INFO",
        "time-sync.corrected",
        "运行时钟偏差已校正",
        device_id=device_id,
        skew_s=round(skew, 1),
    )


async def _maybe_shift(session: Session, device_id: str) -> None:
    """到点且名单里还有人占着，就拨一次。

    拨钟之前先做**预补偿的滚动重布**：截止推过"拨走的一小时 + 窗口"，拨快才不会当场
    断电（实测）。这一行同时是"这台设备正在延长中"的锁——没有它，下一轮扫描会再拨
    一次；命令失败时清掉它，于是下一轮照常重试。
    """
    config = config_store.effective(session, device_id)
    if not config[EXTEND_HOURS_ENABLED_KEY]:
        return
    if crud.get(session, device_id) is not None:
        return  # 已经在延长中
    students = students_of(config, EXTEND_HOURS_STUDENTS_KEY)
    if not students:
        return
    if not _reached(_local_now(device_id, as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])), _extend_hours_at(config)):
        return
    present = _present(session, device_id, students)
    if not present:
        return  # 没人在用，不必拨

    # 先记下谁在哪个喷头，再下发拨钟：会话会被清掉，那一刻之后再没有别的地方能说出
    # "他原来在哪个喷头"。
    assignments = [{"door_no": door_no, "cert_no": cert_no} for door_no, cert_no in present]
    shift_s = _shift_s(config)
    zone = as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])
    local_now = _local_now(device_id, zone)
    if local_now is None:
        log_event("WARNING", "extend-hours.no-skew", "还没有这台设备的钟偏差记录，不动它", device_id=device_id)
        return
    # 落点守卫：拨快后的钟面必须跨过午夜（一次跳过新人关闭窗口与断电时刻），否则本轮放弃
    # landing 与 local_now 同轴（表盘），日期不前进就是没跨过午夜。
    landing = shift_rfc3339(local_now, shift_s)
    landing_date = parse_rfc3339(landing)
    now_date = parse_rfc3339(local_now)
    if landing_date is None or now_date is None or landing_date.date() == now_date.date():
        log_event(
            "WARNING",
            "extend-hours.landing-unsafe",
            "拨快落点未跨过午夜，本轮不拨",
            device_id=device_id,
            landing=landing,
        )
        return
    crud.record(session, device_id, utcnow(), assignments)
    if not await _roll_power(session, device_id, config, pre_shift=True):
        crud.clear(session, device_id)
        _record("shift-failed", "保护性重布没成功，下一轮再试", device_id, level="WARNING")
        return
    if not await _set_clock(session, device_id, shift_s, zone):
        crud.clear(session, device_id)
        _record("shift-failed", "拨钟没成功，下一轮再试", device_id, level="WARNING")
        return
    # shifted_at 以**拨成功那一刻**为准：恢复条件（被跳过的断电时刻已过）从它起算。
    crud.record(session, device_id, utcnow(), assignments)
    _record("shifted", "已延长服务时间", device_id, students=len(present))


# ── 入口之一：普通帧到达 ────────────────────────────────────────────────────


def on_frame(device_id: str) -> None:
    """帧到达时调：延长期间把名单里的人分配回原喷头。

    **同步、立刻返回**。帧的处理路径上不能等命令——重新分配要几秒（一次分配是整轮界面
    流程），期间设备收不到回执会重发同一帧，而每重发一次又排一次分配。所以真正的分配推到
    后台任务里，这里只做"要不要做"的判断（查一行表、查一次会话，都是本地读）。

    三种情形直接返回：**不在延长中**（表里没行）、**没什么可分配的**、**名单是空的**。
    还有一种：**已经有一轮在跑**——帧与定时任务都会推这一步，两边同时动手会给同一个人
    下发两条命令。
    """
    with session_scope() as session:
        row = crud.get(session, device_id)
        if row is None:
            return
        shift = _as_shift(row)
        if not shift.assignments:
            return
        if _reassign_tasks:
            return
        students = students_of(config_store.effective(session, device_id), EXTEND_HOURS_STUDENTS_KEY)
    if not students:
        return
    task = asyncio.create_task(_reassign_task(shift, students))
    # 持有引用是必须的：asyncio 只保留弱引用，一个被丢弃的 Task 可能在跑完之前就被回收
    _reassign_tasks.add(task)
    task.add_done_callback(_reassign_tasks.discard)


async def _reassign_task(shift: Shift, students: dict[str, str]) -> None:
    """asyncio 任务入口：自己开短会话（帧处理路径没有请求上下文）。"""
    from app.db.session import session_scope

    with session_scope() as session:
        await _reassign(session, shift, students)


async def _reassign(session: Session, shift: Shift, students: dict[str, str]) -> None:
    """把名单里的人分配回他们原来那个喷头——**每人一次**。

    五种情形分得开，这是这一层唯一要紧的判断：

    - **拨钟前的会话还挂着**（浴室端还没清场，拨钟刚完成的那一瞬间）→ **等**，不标记。
      拨钟与清场之间隔着"浴室 App 下一轮扫描 + 帧往返"（2026-10-14 实测 0.9 秒），而
      本函数会被同一轮 tick 紧跟着触发——此刻看到的会话是**马上要被清掉的残留**，把它
      当成"放回已成立"就是那晚的真 bug：他整晚没被放回，直到自己去设备前重开。
      "等"以会话的**建立时刻**为据（`assigned_at` 与 `shifted_at` 同轴）：拨钟前的
      会话是被清的对象，拨钟后的才是"已成立"——后者覆盖两种人：**已经放回过**的
      （本次延长里他重新有了会话，无论这条流程办的还是他自己），以及**他自己
      重新开了一个**。放回是**一次性**的：拨钟清场之后把人放回原位，仅此而已。
      浴室端之后按自己的 300 秒无水门槛收回他，那是**他走了**的信号——不放回，
      拨回条件（喷头全部空闲）才有成立的一天。放回若做成循环，人走了也会被反复
      重新预约，延长永远不结束，滚动重布通宵跑（2026-10-06 深夜实测复现：收回后
      1 秒就被重新分配，整夜循环）。
    - **前置拒**（门还被别人占着、他刚有了别的会话）→ 跳过这个人，下次再来。这时命令
      **根本没下发**，所以"下次再来"不是重试。
    - **命令失败**（超时、离线、被浴室端拒而报了 ok 之后仍不占用）→ 记一笔，**这个人不再试**。
      命令真发出去而没成就是失败；每轮再发一遍同样的命令不会有不同的结果。
    """
    device_id = shift.device_id
    # 按学号索引现占：判"已成立"要看会话的**建立时刻**，而 `open_session_door` 只给门号。
    occupying = {item.cert_no: item for item in doors_service.open_sessions(session, device_id)}
    for door_no, cert_no in shift.assignments:
        password = students.get(cert_no)
        if password is None:
            log_event("INFO", "extend-hours.students-changed", "他不在名单里了，不分配", device_id=device_id)
            continue
        if (device_id, cert_no) in _reassigned or (device_id, cert_no) in _reassign_failed:
            continue
        door_session = occupying.get(cert_no)
        if door_session is not None and door_session.assigned_at >= shift.shifted_at:
            # 拨钟之后才建立的会话：他自己重新开了（或我们已经放过），放回已成立
            _reassigned.add((device_id, cert_no))
            continue
        if door_session is not None:
            # 拨钟之前就有的会话还没被清场（拨钟刚完成的那一瞬间）：等，不标记
            continue
        try:
            result = await sessions_service.perform_assign(
                session,
                device_id,
                AssignRequest(door_no=door_no, cert_no=cert_no, password=password),
            )
        except ApiProblemError as exc:
            log_event(
                "INFO",
                "extend-hours.reassign-waiting",
                "重新分配还轮不到，下次再来",
                device_id=device_id,
                door_no=door_no,
                problem=exc.problem.code,
            )
            continue
        # 命令本身失败时领域层**不抛**，只把结果返回来（它只在前置检查上抛）。不看这一句
        # 就会记下一条"已分配回去"的假日志，而那个门其实空着——重布之后再没有帧可等，
        # 也没人会来纠正它。
        if result.get("status") != outcomes.OK.value:
            _reassign_failed.add((device_id, cert_no))
            _record(
                "reassign-failed",
                "分配没成功，这次不替他再试了",
                device_id,
                level="WARNING",
                door_no=door_no,
                cert_no=cert_no,
                reason_code=str(result.get("reason_code") or "-"),
            )
            continue
        _reassigned.add((device_id, cert_no))
        _record("reassigned", "已把他分配回原喷头", device_id, door_no=door_no, cert_no=cert_no)


#: 在途的重新分配任务。见 [on_frame]：它们必须被持有，否则可能被提前回收。
_reassign_tasks: set[asyncio.Task[None]] = set()


# ── 入口之二：定时任务 ──────────────────────────────────────────────────────


async def tick_once() -> None:
    """扫一遍：延长服务（该拨的拨、正在延长的推进）与自动校时（写准偏差）。

    两个职责共用一个扫描节奏但互不依赖（见下方两个内部函数）。离线设备不需要
    预先筛掉——它上面不会有新会话，"名单里有人占用"自然为假；命令失败时也会
    如实回报。每台设备用**自己的**有效配置推进——时区、拨快幅度、电源计划都是
    设备级的，借用别台的值会把滚动目标拨到另一块表盘上。

    会话在本协程内开、内关（见 core/background 的模块说明）。
    """
    with session_scope() as session:
        devices = devices_service.list_devices(session)
        await _advance_extensions(session, devices)
        await _correct_clocks(session, devices)


async def _advance_extensions(session: Session, devices: list[DeviceRecord]) -> None:
    """延长服务的推进：该拨的拨（_maybe_shift 自己判"是否已在延长中"），正在延长的
    滚动续写、推分配、看恢复条件。正在延长的只在 clock_shifted_at 有值的那些上。"""
    for record in devices:
        await _maybe_shift(session, record["device_id"])
    for row in crud.list_shifted(session):
        shift = _as_shift(row)
        # 行跟着设备走（外键 CASCADE）：设备除档时这一行随之消失，不会有残行。
        await _advance(session, shift, config_store.effective(session, shift.device_id))


async def _correct_clocks(session: Session, devices: list[DeviceRecord]) -> None:
    """自动校时的推进：对每台设备判偏差阈值，超了就写准。与延长服务无共享状态。"""
    for record in devices:
        config = config_store.effective(session, record["device_id"])
        await _maybe_correct_clock(session, record["device_id"], config)


async def _advance(session: Session, shift: Shift, config: ConfigValues) -> None:
    """推进一台正在延长的设备：滚动续写截止、推重新分配、检查恢复条件。

    三个动作每轮都做、顺序固定：滚动续写最要紧（它是"谁都不会被打断"的保证）；
    重新分配幂等（查现状决定做不做）；恢复只在条件齐了才动。续写有**限频**——
    武装未满 2 分钟不重复下发（命令量降为 1/4，保证不变）；失败不计入限频，
    下一轮 tick 照常重试。
    """
    last = _last_roll_at.get(shift.device_id)
    if last is None or time.monotonic() - last >= ROLL_REARM_INTERVAL_S:
        await _roll_power(session, shift.device_id, config, pre_shift=False)
    students = students_of(config, EXTEND_HOURS_STUDENTS_KEY)
    if students:
        await _reassign(session, shift, students)
    if _restore_due(session, shift, config):
        await _restore(session, shift, config)


def _restore_due(session: Session, shift: Shift, config: ConfigValues) -> bool:
    """可不可以拨回了：被跳过的断电时刻已过，且设备上无未结束会话。

    两个条件缺一不可——时刻没到，延长还在服务窗口里；有会话在（预约未开阀的、
    淋浴中的），拨回的钟跳会清掉这些会话。"尽可能连续不被打断"由这两条共同保证。
    """
    zone = as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])
    shift_local = to_local(shift.shifted_at, zone)
    now_local = to_local(now_rfc3339(), zone)
    if shift_local is None or now_local is None:
        return False
    if now_local.date() <= shift_local.date():
        return False  # 午夜未过：敌对窗口的尾巴还在
    return not doors_service.open_sessions(session, shift.device_id)


async def _restore(session: Session, shift: Shift, config: ConfigValues) -> bool:
    """拨回设备钟，并在恢复后的框架里重布常规计划；返回是否完全成功。

    先拨回再重布：重布目标（`power_off_at` / `power_on_at`）要按**拨回之后**的钟面换算。
    拨回本身清不掉任何人——能走到这里，设备上已经没有未结束的会话了。

    拨回成功而重布失败时**保留那一行**：钟已经归位，但 MCU 里的截止还是滚动窗口留下
    的旧值，下一轮滚动会续写、本函数也会重试重布，直到成功。
    """
    zone_offset_minutes = as_int(config[SERVER_ZONE_OFFSET_MINUTES_KEY])
    if not await _set_clock(session, shift.device_id, -_shift_s(config), zone_offset_minutes):
        _record("restore-failed", "调回没成功，下一轮再试", shift.device_id, level="WARNING")
        return False
    rearmed = await _schedule_power(
        session,
        shift.device_id,
        str(config[POWER_OFF_AT_KEY]),
        str(config[POWER_ON_AT_KEY]),
    )
    if rearmed:
        crud.clear(session, shift.device_id)
        _forget_failures(shift.device_id)
        _record("restored", "设备钟已调回，节奏已恢复", shift.device_id)
    else:
        _record(
            "restore-rearm-failed",
            "钟已调回但常规计划没重布，下一轮滚动会续写、本函数会重试",
            shift.device_id,
            level="ERROR",
        )
    return rearmed


def _forget_failures(device_id: str) -> None:
    """这台设备的延长结束了，把放回与失败的记忆都清掉——下一次延长从零开始。"""
    for key in [item for item in _reassign_failed if item[0] == device_id]:
        _reassign_failed.discard(key)
    for key in [item for item in _reassigned if item[0] == device_id]:
        _reassigned.discard(key)


def _reached(local_now: str | None, at: str) -> bool:
    """设备表盘上是否已经走过 `HH:MM` 那一刻。

    **两个时刻都在"设备表盘"这条轴上**，所以可以直接比大小。时刻取不出来（还没有偏差
    记录）或配置值不合法时一律给 False：判不了就不动手，下一轮再看。
    """
    if local_now is None:
        return False
    now_at = parse_rfc3339(local_now)
    target_at = parse_rfc3339(_today_at(local_now, at))
    if now_at is None or target_at is None:
        return False
    return now_at >= target_at


def _today_at(local_now: str, at: str) -> str:
    """把 `HH:MM` 拼到 `local_now` 的那一天上，得到同一天同一轴上的 RFC 3339 时刻。

    日期取 `local_now` 的日期部分——它是**设备表盘上的**那一天（那个字符串的轴已经换
    过了，只是用 RFC 3339 写出来），所以"今天 23:49"与"表盘现在"落在同一天上。
    """
    return f"{local_now[:10]}T{at}:00.000Z"


# 一轮的逻辑见 [tick_once]；启动、取消、间隔由 core/background 统一管理。
