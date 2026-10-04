"""延长服务时间：到点把设备钟拨走，让人在规定时间之外还能用。

## 它做的事

浴室 App 每天按 `autoCloseTime`（实测 23:58）关机，并在关机前五分钟放掉所有喷头
（`ARCHITECTURE.md` 那四个释放条件里的"关机前 5 分钟"）。对名单里的人来说那一下是
提前收场。这里在更早的某一刻（`extend_hours_at`，默认 23:49）把设备钟往后拨一小时、
然后重启浴室 App，于是：

- "关机前五分钟"落到**次日**的同一时刻上，当晚不再放人；
- 重启让 App 重新装载每日开关机计划，MCU 里那条关机延时跟着推到次日
  （`docs/device-poweroff.md`）；
- 而 App 的释放条件全用绝对值（`|openTime − now|`），钟一跳它们统统越界——**设备上
  所有喷头被清空**，不只是名单里的那几个。

所以拨完之后要等门清空，再替名单里的人把喷头分配回去。

## 三个动作，顺序不能变

```
① 拨钟      clock.set(+1h)     —— 浴室 App 还在跑，它下一轮扫描就会清空喷头
② 重新分配  session.assign     —— 趁 App 还在前台，把名单里的人放回原喷头
③ 重启      bath.restart       —— 让它带着新钟重读开关机计划，MCU 的延时才跟着走
```

**②必须在③之前**：重启的第一条 shell 就是 `am force-stop`，而分配是驱动 App 界面的
自动化——顺序反了或挨着发，分配连开始都开始不了。而**③又必须在①之后**：App 在启动那一刻
读系统时钟装载计划，先重启的话它读到的还是旧时刻。

中间那一步要等：拨钟到清空之间隔着浴室 App 的一轮扫描（≤1 秒），而"清空完成"只有它的
写库能告诉我们。等不到就放弃那几个人（`ASSIGN_WINDOW_S`），**但不能因此不重启**——重启
关系到 MCU，比"把人放回去"更要紧。

## 什么时候收场

拨钟**成功**那一刻起算，满 `extend_hours_hold_s` 就把钟调回，并再重启一次浴室 App
让它按真实时刻重算计划。**到期时还挂着的会话会被浴室端清掉**：它判
`|开阀时刻 − 此刻|` 用的是绝对值，钟往回一跳那个差值就跳过去了。借来的一小时要还，
这是这个功能自身的终点，不是缺陷。

收尾这两件可以连做，与拨进不同：那时不需要把谁分配回去，所以中间没有"等分配结束"。

## 两个入口，各管一段

- [on_frame]：普通帧到达时调，推**重新分配**那一步。拨钟会让浴室 App 清空所有喷头，
  而清空必然写库、必然出一帧，所以"等门空"不需要轮询。
- [tick_once]：定时任务每半分钟调一次。它负责**拨钟**与**调回**——那两件事到点时没有帧
  可等（帧是事件驱动的，状态不变就不报；而调回到期落在真实次日 00:49，那时浴室早没人
  了），只能自己看表。它还顺带推一次重新分配：帧平均四分钟一份，而那一步等不起。

## 为什么不与另外三个后台任务共用调度器

`sessions/scheduler` 的候选是**每个喷头一个**（`FEATURES` 那张表的 `states` / `start`
全是喷头级），而这里问的是"**整台设备**上还有没有名单里的人"。粒度不同，硬塞进去要在
那张表里放一个不成立的形状。另外三条还有一个更实在的差别：它们的记忆在内存里，丢了
只是少续一次；这一个丢了设备钟会一直偏着，所以状态落库（见 `repository.py`）。

## 与另外三条的边界只有一处

拨钟会把它们的会话清掉。那是既成事实，不是接口——浴室 App 自己清，我们没有调用它们、
它们也不需要知道我们。下一帧它们看到的只是"人没了"。
"""

import asyncio
from dataclasses import dataclass
from sqlite3 import Row

from app.core import actions, device_clock, outcomes
from app.core.clock import now_rfc3339, parse_rfc3339, seconds_between, shift_rfc3339, wall_clock_text
from app.core.logging import exc_info_text, log_event
from app.core.problems import ApiProblemError
from app.core.protocol import as_int
from app.modules.config import store as config_store
from app.modules.config.schemas import ConfigValues
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.sessions import routes as sessions_routes
from app.modules.sessions import service as sessions_service
from app.modules.sessions.decision import roster
from app.modules.sessions.schemas import AssignRequest

from . import repository

#: 拨钟拨走的时长。**不做配置**：它的作用是把设备钟推过日界，而 `extend_hours_at`
#: 的下限（23:00）保证"加满一小时即跨日"。多拨没有别的好处，少拨则跨不过去——跨不
#: 过去意味着浴室 App 的下一次扫描仍在当日的 23:53 命中，当场把所有人放掉。
SHIFT_S = 3600

#: 扫表间隔。它要远小于"到点"与"浴室 App 放人"之间那几分钟余量（`extend_hours_at`
#: 默认 23:49，放人线 23:53）。
TICK_INTERVAL_S = 30.0

#: 拨钟之后留给"重新分配"的窗口。过了就不再试、直接重启。
#:
#: 它一次封三件事：分配不会无限试、重启不会无限等、MCU 能在死线前拿到新延时。死线是
#: **真实 23:58**——MCU 存的是相对延时、从上次写入起算，而拨钟不改它。60 秒的余量按实测
#: 算：一次 `session.assign` 的 p90 是 4.1 秒（生产 `command_audit`），设备 7 个喷头即名单
#: 最多 7 人，29 秒是上限，窗口是它的两倍。
ASSIGN_WINDOW_S = 60.0

_task: asyncio.Task[None] | None = None

#: (设备, 学号) → 这次延长里我们已经替他分配过、而命令失败了。
#:
#: 它与"前置拒"分得开：前置拒（门还占着）的命令**根本没下发**，下次还要试；命令真发出去
#: 而失败了就是失败，不再试——否则同一个人每轮都发一遍同样的命令，永远发不成功。
#: 记忆在内存里，与 `sessions/scheduler` 的 `_attempted` 同类：服务端重启就重来一轮，
#: 代价只是多一条 WARNING，而窗口由库里的 `shifted_at` 算，不受它影响。
_reassign_failed: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class Shift:
    """一次拨钟：哪台设备、什么时候拨成功、**有没有重启过**、拨之前谁在哪个喷头。"""

    device_id: str
    shifted_at: str
    restarted_at: str
    assignments: tuple[tuple[int, str], ...]


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


def _present(device_id: str, students: dict[str, str]) -> list[tuple[int, str]]:
    """这台设备上此刻还占着喷头的名单里的人：(喷头号, 学号)。

    用**未结束的会话**当"占用"，而不是读喷头现态：占着的正是会话，而"重新分配"要的
    也是会话的主人。两者在正常路径上一致，而会话是更贴切的那个。
    """
    return [
        (session.door_no, session.cert_no)
        for session in doors_service.open_sessions(device_id)
        if session.cert_no in students
    ]


def _as_shift(row: Row) -> Shift:
    assignments = tuple(
        (as_int(item.get("door_no")), str(item.get("cert_no", "")))
        for item in repository.parse_assignments(row)
        if isinstance(item, dict)
    )
    return Shift(
        device_id=str(row["device_id"]),
        shifted_at=str(row["shifted_at"]),
        restarted_at=str(row["restarted_at"] or ""),
        assignments=assignments,
    )


# ── 动作 ──────────────────────────────────────────────────────────────────


async def _set_clock(device_id: str, seconds: float, zone_offset_minutes: int) -> bool:
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

    written = await sessions_service.send_command_and_wait(device_id, actions.CLOCK_SET, {"datetime": text})
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

    log_event(
        "INFO", "extend-hours.clock-moved", "设备钟已平移", device_id=device_id, seconds=int(seconds), target=text
    )
    return True


async def _restart_bath_app(device_id: str) -> bool:
    """重启浴室 App，让它带着当前时钟重读每日开关机计划；返回是否成功。

    **它必须晚于写时钟**：App 在启动那一刻读系统时钟装载计划（`WelcomeActivity` →
    `smdtSetTimingSwitchMachine`），先重启再拨钟的话它读到的还是旧时刻，MCU 里那条延时
    也就没跟着走——整件事白做。

    **它也必须晚于重新分配**：`force-stop` 会杀掉 App，而重新分配是驱动 App 界面的自动化。
    实测（生产 `command_audit`）`clock.set` 的 p50 只有 0.6 秒，而重启命令的第一条就是
    `am force-stop`——两件挨着发的话，分配连开始都开始不了。
    """
    restarted = await sessions_service.send_command_and_wait(device_id, actions.BATH_RESTART, {})
    if restarted["status"] != outcomes.OK.value:
        log_event(
            "WARNING",
            "extend-hours.restart-failed",
            "浴室 App 重启失败，开关机计划没跟着新钟重算",
            device_id=device_id,
            reason_code=restarted["reason_code"] or "-",
        )
        return False
    return True


# ── 入口之一：普通帧到达 ────────────────────────────────────────────────────


def on_frame(device_id: str) -> None:
    """帧到达时调：延长期间把名单里的人分配回原喷头。

    **同步、立刻返回**。帧的处理路径上不能等命令——重新分配要几秒（一次分配是整轮界面
    流程），期间设备收不到回执会重发同一帧，而每重发一次又排一次分配。所以真正的分配推到
    后台任务里，这里只做"要不要做"的判断（查一行表、查一次会话，都是本地读）。

    四种情形直接返回：**不在延长中**（表里没行）、**没什么可分配的**、**名单是空的**、
    **已经重启过了**（分配阶段结束，往后轮到调回）。还有一种：**已经有一轮在跑**——帧与
    定时任务都会推这一步，两边同时动手会给同一个人下发两条命令。
    """
    row = repository.get(device_id)
    if row is None:
        return
    shift = _as_shift(row)
    if shift.restarted_at or not shift.assignments:
        return
    if _window_passed(shift):
        return
    if _reassign_tasks:
        return
    students = roster(config_store.effective(device_id), "extend_hours_students")
    if not students:
        return
    task = asyncio.create_task(_reassign(shift.device_id, shift.assignments, students))
    # 持有引用是必须的：asyncio 只保留弱引用，一个被丢弃的 Task 可能在跑完之前就被回收
    _reassign_tasks.add(task)
    task.add_done_callback(_reassign_tasks.discard)


async def _reassign(device_id: str, assignments: tuple[tuple[int, str], ...], students: dict[str, str]) -> None:
    """把名单里的人分配回他们原来那个喷头。

    三种情形分得开，这是这一层唯一要紧的判断：

    - **已经在占用态** → 跳过。靠"查现状"决定做不做，所以重复调用无害，也不必另记状态。
    - **前置拒**（门还被别人占着、他刚有了别的会话）→ 跳过这个人，下次再来。这时命令
      **根本没下发**，所以"下次再来"不是重试。
    - **命令失败**（超时、离线、被浴室端拒而报了 ok 之后仍不占用）→ 记一笔，**这个人不再试**。
      命令真发出去而没成就是失败；每轮再发一遍同样的命令不会有不同的结果。
    """
    for door_no, cert_no in assignments:
        password = students.get(cert_no)
        if password is None:
            log_event(
                "INFO", "extend-hours.roster-changed", "他不在名单里了，不分配", device_id=device_id, cert_no=cert_no
            )
            continue
        if (device_id, cert_no) in _reassign_failed:
            continue
        if doors_service.open_session_door(device_id, cert_no) is not None:
            continue
        try:
            result = await sessions_routes.perform_assign(
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
                cert_no=cert_no,
                problem=exc.problem.code,
            )
            continue
        # 命令本身失败时领域层**不抛**，只把结果返回来（它只在前置检查上抛）。不看这一句
        # 就会记下一条"已分配回去"的假日志，而那个门其实空着——重启之后再没有帧可等，
        # 也没人会来纠正它。
        if result.get("status") != outcomes.OK.value:
            _reassign_failed.add((device_id, cert_no))
            log_event(
                "WARNING",
                "extend-hours.reassign-failed",
                "分配没成功，这次不替他再试了",
                device_id=device_id,
                door_no=door_no,
                cert_no=cert_no,
                reason_code=result.get("reason_code") or "-",
            )
            continue
        log_event(
            "INFO",
            "extend-hours.reassigned",
            "已把他分配回原喷头",
            device_id=device_id,
            door_no=door_no,
            cert_no=cert_no,
        )


#: 在途的重新分配任务。见 [on_frame]：它们必须被持有，否则可能被提前回收。
_reassign_tasks: set[asyncio.Task[None]] = set()


# ── 入口之二：定时任务 ──────────────────────────────────────────────────────


async def tick_once() -> None:
    """扫一遍：该拨的拨，正在延长的往前走一步。

    两类设备分开取：**该拨的**在任何设备上都可能发生（所以遍历全部设备，由 `_maybe_shift`
    自己判"这台是不是已经在延长中"），**正在延长的**只在 `clock_shift` 里有行的那些上面。
    离线设备不需要预先筛掉——它上面不会有新会话，"名单里有人占用"自然为假；命令失败时
    也会如实回报。
    """
    for record in devices_service.list_devices():
        await _maybe_shift(record["device_id"])
    for row in repository.list_shifted():
        await _advance(_as_shift(row))


async def _advance(shift: Shift) -> None:
    """推进一台正在延长的设备：还没重启就继续分配，重启过就等调回。

    这两个阶段不能并起来：分配要求 App 正在前台跑，而重启会 `force-stop` 它。
    """
    config = config_store.effective(shift.device_id)
    if not shift.restarted_at:
        await _reassign_stage(shift, config)
        return
    await _maybe_restore(shift, config)


async def _reassign_stage(shift: Shift, config: ConfigValues) -> None:
    """拨钟之后、重启之前：把名单里的人分配回原喷头，齐了就重启。

    **两样推动它**：帧到达时 `on_frame` 立刻试一次（快），而每一轮 tick 也试一次（帧是
    事件驱动的，平均四分钟一份，而重排不能只等它）。两边调的是同一个 `_reassign`，它幂等。
    """
    students = roster(config, "extend_hours_students")
    if students and not _window_passed(shift):
        await _reassign(shift.device_id, shift.assignments, students)
    if not _reassign_done(shift, students):
        return
    if not await _restart_bath_app(shift.device_id):
        # 下一轮再试：重启让 App 重读开关机计划，而计划决定 MCU 什么时候断电
        return
    repository.mark_restarted(shift.device_id, now_rfc3339())
    log_event("INFO", "extend-hours.restarted", "浴室 App 已重启，开关机计划已重读", device_id=shift.device_id)


def _window_passed(shift: Shift) -> bool:
    """留给分配的窗口过了没有。过了就不再试，直接进下一步。"""
    return seconds_between(shift.shifted_at, now_rfc3339()) >= ASSIGN_WINDOW_S


def _reassign_done(shift: Shift, students: dict[str, str]) -> bool:
    """可不可以重启了：名单里的人要么已分配回去，要么已经管不着。

    "管不着"有两种：他不在名单里了（配置改了），或者窗口过了（不能再等）。三种之外的
    情形一律继续等——**这个函数只回答"齐了没有"，不下发**。
    """
    if _window_passed(shift):
        return True
    for _door_no, cert_no in shift.assignments:
        if cert_no in students and doors_service.open_session_door(shift.device_id, cert_no) is None:
            return False
    return True


async def _maybe_shift(device_id: str) -> None:
    """到点且名单里还有人占着，就拨一次。**只拨钟，不重启**——重启要等分配结束。"""
    config = config_store.effective(device_id)
    if not config["extend_hours_enabled"]:
        return
    if repository.get(device_id) is not None:
        return  # 已经在延长中
    students = roster(config, "extend_hours_students")
    if not students:
        return
    if not _reached(
        _local_now(device_id, as_int(config["extend_hours_zone_offset_minutes"])), str(config["extend_hours_at"])
    ):
        return
    present = _present(device_id, students)
    if not present:
        return  # 没人在用，不必拨

    # 先记下谁在哪个喷头，再下发拨钟：会话会被清掉，那一刻之后再没有别的地方能说出
    # "他原来在哪个喷头"。这一行同时是"这台设备正在延长中"的锁——没有它，下一轮扫描会
    # 再拨一次。命令失败时清掉它，于是下一轮照常重试。
    assignments = [{"door_no": door_no, "cert_no": cert_no} for door_no, cert_no in present]
    repository.record(device_id, now_rfc3339(), assignments)
    if not await _set_clock(device_id, SHIFT_S, as_int(config["extend_hours_zone_offset_minutes"])):
        repository.clear(device_id)
        log_event("WARNING", "extend-hours.shift-failed", "拨钟没成功，下一轮再试", device_id=device_id)
        return
    # 保持时长从**拨成功那一刻**起算：写成"决定要拨那一刻"的话，命令失败重试到成功时
    # 这段时长可能早就过了，会立刻调回，等于没拨
    repository.record(device_id, now_rfc3339(), assignments)
    log_event("INFO", "extend-hours.shifted", "已延长服务时间", device_id=device_id, students=len(present))


async def _maybe_restore(shift: Shift, config: ConfigValues) -> None:
    """保持时长满了就把钟调回，并再重启一次让计划按真实时刻重算。

    这两件可以同一轮连做，与拨进不同：延长的收尾不需要把谁分配回去，所以中间没有
    "等分配结束"那一段。
    """
    hold_s = as_int(config["extend_hours_hold_s"])
    if seconds_between(shift.shifted_at, now_rfc3339()) < hold_s:
        return
    zone_offset_minutes = as_int(config["extend_hours_zone_offset_minutes"])
    if not await _set_clock(shift.device_id, -SHIFT_S, zone_offset_minutes):
        log_event("WARNING", "extend-hours.restore-failed", "调回没成功，下一轮再试", device_id=shift.device_id)
        return
    # 钟已经回去了；重启失败也不能再把行留着——留着会被当成"还偏着"，下一轮又调一次
    if not await _restart_bath_app(shift.device_id):
        log_event("WARNING", "extend-hours.restore-restart-failed", "钟已调回但计划没重读", device_id=shift.device_id)
    repository.clear(shift.device_id)
    _forget_failures(shift.device_id)
    log_event("INFO", "extend-hours.restored", "设备钟已调回", device_id=shift.device_id)


def _forget_failures(device_id: str) -> None:
    """这台设备的延长结束了，把"替他试过而失败了"的记忆清掉。"""
    for key in [item for item in _reassign_failed if item[0] == device_id]:
        _reassign_failed.discard(key)


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


# ── 生命周期 ──────────────────────────────────────────────────────────────


async def _tick_loop() -> None:
    while True:
        try:
            await tick_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 一轮失败不能带垮服务，下一轮重试
            log_event(
                "ERROR", "extend-hours.tick-failed", "延长服务时间的定时任务失败，等下一轮重试", error=exc_info_text()
            )
        await asyncio.sleep(TICK_INTERVAL_S)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_tick_loop())
        log_event("INFO", "extend-hours.started", "延长服务时间已启动")


def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
        log_event("INFO", "extend-hours.stopped", "延长服务时间已停止")
