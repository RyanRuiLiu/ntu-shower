"""后台任务的调度与执行：把判据（`decision.py`）接到真实的时钟、队列与设备上。

## 为什么不是定时扫描

帧是**事件驱动**的（状态变化才上报），而"会话逼近 20 分钟"是个**时间点**、不是事件：
纯事件驱动永远不触发。而纯定时扫描要为扫描间隔留余量——那等于让学生少洗那段时间。
所以两样都要，但各只做自己擅长的：

- **到点**由一次性定时器提醒（每喷头一个，指着"下一个该动手的时刻"）
- **状态变化**由帧驱动重算（帧一来就重算那个喷头该什么时候动手）
- **续期的第二步**（结算之后的分配）也由帧驱动：门被释放必然导致浴室 App 写库、必然
  出一帧，所以"等门变空闲"不需要轮询

## 为什么每喷头一个定时器

一个喷头会随会话推进依次落到不同的判据上，而**已暂停**同时落在"会话续期"与"自动结算"
两条上（见 `decision.py`）。每功能各挂一个定时器会互相打架（取消与重挂交错），所以每
喷头只留**一个**、指着最早的那个；状态一变就重算，它自己会纠正。

## 串行

`sessions.service.send_command_and_wait` 已经按 (设备, 通道) 加了串行锁，而 assign 与
settle 同属 automation 通道，所以**单条命令**不会并发。这里再加一层**每设备一个队列 +
一个 worker**，是为了让"结算 → 分配"这个**两步序列**原子化：不串行的话两场续期会交错
成 settle(A) settle(B) … assign(A) assign(B)，而每一步都要读服务端现态。

## 走领域层，不另写一份判据

结算与分配都经 `sessions.routes` 的 `perform_settle` / `perform_assign`——那是"所有调用
方共用"的入口，前置检查（可分配状态、重复会话、设备守卫）都在里面。**直接调
`send_command_and_wait` 会把那些前置整条绕过去**，等于同一个判据有两个出处。

领域层拒了要分两类：门还被占着、他还有会话、他已经走了，都只是"现在还不该做"，留待办等
下一帧（命令根本没下发，所以不是重试）；设备离线、通道积压那类才是真出问题，要人看见。

## 单次执行

每个会话每个功能**只动手一次**，失败不重试。这不只是取向问题：失败后重算出的到点还是
同一刻，不记一笔就会自旋。记在 `_attempted` 里（键含设备——会话 id 是全库自增的），
会话结束时清掉。

## 判据只认命令结果

命令报 ok 只说明设备把那一轮 UI 流程走完了——浴室端拒绝（比如密码错）时它**也报 ok**
（`AutomationFlow.awaitTerminal` 的提示页分支返回 `reached = true`，见 `CommandScheduler
.execute` 里 `error == null` 那条出口）。所以"浴室端到底认没认"我们不看，只看命令结果；
看错的后果是**没帮上忙**（那个学生被浴室端释放、自己重新分配一次），不是帮倒忙。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

from app.core import actions, outcomes, problems
from app.core.clock import now_rfc3339, shift_rfc3339
from app.core.logging import log_event
from app.core.problems import ApiProblemError
from app.core.protocol import as_int
from app.db.session import session_scope
from app.modules.audit import repository as audit_repository
from app.modules.config import store as config_store
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState

from . import routes as sessions_routes
from .decision import (
    FEATURES_BY_KEY,
    Candidate,
    Trigger,
    decide,
    password_of,
    recheck,
    still_allowed,
)
from .schemas import AssignRequest, CommandOutcome, SettleRequest

#: 结算成功之后等"门变空闲"的那一帧，最多等这么久。没有它的话，设备离线几小时再上线，
#: 第一帧会去执行一条陈旧的待分配——**给一个早已走掉的学生开一场会话**。
#: 它是机制的时限、不是业务旋钮，所以是常量：能调的话，调大只会让那种错更容易发生。
ASSIGN_PENDING_TIMEOUT_S = 30.0

#: 结算时服务端已经找不到"该学号占用中的喷头"（`problems.NO_OCCUPIED_DOOR`）。那正是"他已经
#: 走了、或者他自己已经结过账了"，是正常收尾，不该刷告警。
#:
#: 另外两类**不是失败、只是"现在还不该做"**：门还被占着（`DOOR_NOT_FREE`）、他名下还有
#: 未结束的会话（`DUPLICATE_SESSION`）。它们由领域层的前置检查给出，命令根本没下发，
#: 所以等下一帧再来不是"重试"。
_WAIT_PROBLEMS = frozenset({problems.DOOR_NOT_FREE.code, problems.DUPLICATE_SESSION.code})
_DONE_PROBLEMS = frozenset({problems.NO_OCCUPIED_DOOR.code})

#: `door_live.state` 里我们认识的取值。认不出来的当空闲——宁可不动手，也不要对一条没读懂
#: 的现态下单。
_STATES = {state.value for state in DoorState}


# ── 待办 ──────────────────────────────────────────────────────────────────


class _Auditable(Protocol):
    """审计那一行要的字段。三种待办（结算、补分配、等门变空的待办）都暴露这一套，
    所以落表与记日志那处不必按待办类型分叉。"""

    @property
    def device_id(self) -> str: ...

    @property
    def door_no(self) -> int: ...

    @property
    def cert_no(self) -> str: ...

    @property
    def feature(self) -> str: ...

    @property
    def session_id(self) -> int: ...

    @property
    def threshold_s(self) -> int: ...

    @property
    def action(self) -> str: ...


@dataclass
class _SettleJob:
    """一次结算。续期类功能在结算成功之后还要补一次分配。"""

    candidate: Candidate
    feature: str
    threshold_s: int

    # 与 _AssignJob 暴露同一套只读字段：审计那一行要的东西两处一样，
    # 收口后调用处读法一致，不必按待办类型分叉。
    @property
    def device_id(self) -> str:
        return self.candidate.device_id

    @property
    def door_no(self) -> int:
        return self.candidate.door_no

    @property
    def cert_no(self) -> str:
        return self.candidate.cert_no

    @property
    def session_id(self) -> int:
        return self.candidate.session_id

    @property
    def action(self) -> str:
        return actions.SESSION_SETTLE


@dataclass
class _AssignJob:
    """补上那一次分配。`session_id` 是**被结算的那一场**——续期两步共用它，查起来是一件事。"""

    device_id: str
    door_no: int
    cert_no: str
    password: str
    feature: str
    session_id: int
    threshold_s: int

    @property
    def action(self) -> str:
        return actions.SESSION_ASSIGN


@dataclass
class _PendingAssign:
    """结算成功、等门变空闲之后要补的那次分配。它在队列之外，由帧推动。"""

    device_id: str
    door_no: int
    cert_no: str
    password: str
    feature: str
    session_id: int
    threshold_s: int
    expires_at: float

    @property
    def action(self) -> str:
        return actions.SESSION_ASSIGN


@dataclass
class _Planned:
    """一个已排定的到点：定时器句柄连同它的计划一起放，读的人才知道"在等什么"。

    只存句柄的话，计划（哪个候选、哪条判据）只活在 `call_at` 的闭包里，外面读不到。
    """

    at: float
    """到点时刻（循环的单调钟）。"""
    candidate: Candidate
    trigger: Trigger
    handle: asyncio.TimerHandle


class _DeviceWorker:
    """一台设备的串行执行者。设备只能串行，串行的边界就是设备。"""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.queue: asyncio.Queue[_SettleJob | _AssignJob] = asyncio.Queue()
        #: 在途的喷头：同一喷头不排两个待办
        self.busy: set[int] = set()
        #: door_no → 该门下一个到点（句柄与计划同放，见 [_Planned]）
        self.timers: dict[int, _Planned] = {}
        self.task: asyncio.Task[None] | None = None


_workers: dict[str, _DeviceWorker] = {}

#: (设备, 喷头) → 待补的那一次分配。键含喷头，因为门是那个被释放的东西。
_pending_assigns: dict[tuple[str, int], _PendingAssign] = {}

#: (设备, 会话, 功能) → 已动手。每个会话每个功能只一次；会话结束时清掉。
#: **键里必须带设备**：会话 id 是全库自增的，只按 (会话, 功能) 记，A 设备的帧会把
#: B 设备的记录一起清掉。
_attempted: set[tuple[str, int, str]] = set()

_running = False


def _record(
    outcome: str,
    message: str,
    subject: _Auditable,
    *,
    level: str = "INFO",
    reason: str = "",
    **extra: object,
) -> None:
    """一次后台任务的终局：**日志与审计表在同一条路上写**。

    收口成一处，是因为每个终局都要发生两次——散在各处写迟早出现"记了日志、没落表"，
    而落表才是这张表存在的理由。`outcome` 用的是日志事件名的后缀（`background-task.<它>`），
    两边同一套说法；`extra` 只进日志（状态码、设备原话这类给人看的细节）。

    待办的字段就是这一行要的字段（见 [_Auditable]）：`feature` / `action` / `session_id`
    / `threshold_s` 都是落库的列，所以这里一处按类型或按功能的分叉都没有。
    """
    log_event(
        level,
        f"background-task.{outcome}",
        message,
        device_id=subject.device_id,
        door_no=subject.door_no,
        feature=subject.feature,
        **extra,
    )
    audit_repository.insert_background_task_run(
        device_id=subject.device_id,
        door_no=subject.door_no,
        cert_no=subject.cert_no,
        feature=subject.feature,
        outcome=outcome,
        action=subject.action,
        session_id=subject.session_id,
        reason=reason,
        threshold_s=subject.threshold_s,
    )


def _worker(device_id: str) -> _DeviceWorker:
    worker = _workers.get(device_id)
    if worker is None:
        worker = _DeviceWorker(device_id)
        _workers[device_id] = worker
    return worker


def _now_s() -> float:
    return asyncio.get_running_loop().time()


# ── 取数：只读派生层 ──────────────────────────────────────────────────────


def _snapshots(device_id: str) -> list[Candidate]:
    """这台设备上此刻所有未结束的会话，连同判据要用的现态。

    取数一律经 `doors` 域公开的功能，不读它的表：会话、现态、串的划断规则都是它的
    事情。原始帧一概不读——判定不需要它，而读了就等于把两套真相都摆上台面。

    现态的关阀时刻已经过钟偏差换算，所以**设备还没有偏差记录时它是空的**（测试里要
    显式建立，否则测的不是线上的样子；线上连上后一个往返内就有，且跨重启持久化）。
    """
    live = doors_service.live_doors(device_id)
    found: list[Candidate] = []
    for session in doors_service.open_sessions(device_id):
        row = live.get(session.door_no)
        if row is None:
            continue  # 会话还在、门的现态却没了：不判，等下一帧
        found.append(
            Candidate(
                session_id=session.session_id,
                device_id=device_id,
                door_no=session.door_no,
                cert_no=session.cert_no,
                state=DoorState(row.state) if row.state in _STATES else DoorState.FREE,
                assigned_at=session.assigned_at,
                paused_at=row.close_time,
                run_started_at=doors_service.run_started_at(device_id, session.door_no, session.cert_no),
            )
        )
    return found


def _plan(device_id: str) -> dict[int, tuple[float, Candidate, Trigger]]:
    """该设备每个喷头下一个到点：`door_no → (到点时刻, 候选, 触发)`。"""
    config = config_store.effective(device_id)
    now = now_rfc3339()
    planned: dict[int, tuple[float, Candidate, Trigger]] = {}
    for candidate in _snapshots(device_id):
        trigger = decide(candidate, config, now)
        if trigger is None:
            continue
        if (candidate.device_id, candidate.session_id, trigger.feature) in _attempted:
            continue  # 这个会话这个功能已经动过手，不再排
        at = _now_s() + trigger.delay_s
        current = planned.get(candidate.door_no)
        if current is None or at < current[0]:
            planned[candidate.door_no] = (at, candidate, trigger)
    return planned


# ── 挂钩点：帧到达、配置保存 ──────────────────────────────────────────────


def on_frame(device_id: str) -> None:
    """一帧刚派生完。重算该设备每个喷头的"下一个到点"，并把待补的分配接上。

    它在事件循环上调用（网关的 `_handle_frame` 是协程），所以可以直接碰定时器。
    """
    if not _running:
        return
    _forget_closed_sessions(device_id)
    reschedule(device_id)
    _flush_pending_assign(device_id)


def reload() -> None:
    """配置刚保存。阈值与名单都变了，所以所有定时器都要重算——**保存即生效**。"""
    if not _running:
        return
    for device_id in list(_workers):
        reschedule(device_id)


def reschedule(device_id: str) -> None:
    """重算该设备的定时器。帧到达、配置保存、一次执行跑完之后都调它。

    **只管定时器**：待补的分配由 [on_frame] 单独推——若放在这里，一次"前置还不满足"的
    分配会在 worker 收尾时再触发一次刷新、再排一次，转成自触发的死循环。
    """
    if not _running:
        return
    worker = _worker(device_id)
    loop = asyncio.get_running_loop()
    planned = _plan(device_id)

    for door_no, existing in list(worker.timers.items()):
        if door_no not in planned:
            existing.handle.cancel()
            worker.timers.pop(door_no, None)

    for door_no, (at, candidate, trigger) in planned.items():
        previous = worker.timers.get(door_no)
        if previous is not None:
            previous.handle.cancel()
        worker.timers[door_no] = _Planned(
            at=at,
            candidate=candidate,
            trigger=trigger,
            handle=loop.call_at(at, _fire, device_id, candidate, trigger),
        )


def _forget_closed_sessions(device_id: str) -> None:
    """会话结束时把它在 `_attempted` 里的记录清掉。

    留着只会让集合一直长；清掉是安全的——那一场不会再触发（它已经结束），重新开的那一场
    有自己的 `session_id`。
    """
    open_ids = {session.session_id for session in doors_service.open_sessions(device_id)}
    for key in [item for item in _attempted if item[0] == device_id and item[1] not in open_ids]:
        _attempted.discard(key)


def snapshot() -> list[dict[str, object]]:
    """调度器此刻的即时计划：每个已排定的到点，连同"为什么是这一刻"的细节。

    给运维可见性用（概览页的后台任务卡）：**只读**——计划由 `reschedule` 维护，这里只
    把它报出来、不做任何判断，判据不会被第二处引用。每行带上门现态、会话号、判据的
    起算时刻与阈值，面板能直接回答"在等谁、等到什么时候、为什么"；到点是循环的单调
    时刻，换算成服务端 RFC 3339 便于直接显示；`time.monotonic()` 与 `loop.time()` 同源
    （asyncio 默认循环的内部钟就是它），所以同步路由的线程池里也能换算。
    """
    now = time.monotonic()
    entries = sorted(
        (planned.at, device_id, door_no, planned.candidate, planned.trigger)
        for device_id, worker in _workers.items()
        for door_no, planned in worker.timers.items()
    )
    rows: list[dict[str, object]] = []
    for at, device_id, door_no, candidate, trigger in entries:
        spec = FEATURES_BY_KEY[trigger.feature]
        started = candidate.assigned_at if spec.start == "assigned_at" else candidate.paused_at
        # 本轮链的用量上限随功能而定（预约续期有、其余没有）；连同起算时刻一起给，
        # 面板才能回答"这一轮什么时候到头"。
        config = config_store.effective(device_id)
        max_duration = as_int(config[spec.max_duration_key]) if spec.max_duration_key is not None else None
        rows.append(
            {
                "device_id": device_id,
                "door_no": door_no,
                "cert_no": candidate.cert_no,
                "feature": trigger.feature,
                "at": shift_rfc3339(now_rfc3339(), at - now),
                "state": candidate.state.value,
                "session_id": candidate.session_id,
                "started_at": started or "",
                "threshold_s": trigger.threshold_s,
                "run_started_at": candidate.run_started_at,
                "max_duration_s": max_duration,
            }
        )
    return rows


# ── 到点与入队 ────────────────────────────────────────────────────────────


def _fire(device_id: str, candidate: Candidate, trigger: Trigger) -> None:
    """到点：复查 → 入队。只做这两件事，重活交给 worker，免得占着事件循环。"""
    _worker(device_id).timers.pop(candidate.door_no, None)
    config = config_store.effective(device_id)
    fresh = {item.session_id: item for item in _snapshots(device_id)}.get(candidate.session_id)

    reason = recheck(fresh, expected=candidate, trigger=trigger, config=config, now=now_rfc3339())
    if reason is not None:
        _record(
            "skipped",
            "到点复查未通过，放弃这一次",
            _SettleJob(candidate=candidate, feature=trigger.feature, threshold_s=trigger.threshold_s),
            reason=reason,
        )
        reschedule(device_id)
        return

    _enqueue_settle(fresh or candidate, trigger.feature, trigger.threshold_s)


def _enqueue_settle(candidate: Candidate, feature: str, threshold_s: int) -> None:
    """把一次结算排进该设备的队列。同一喷头已在途就不再排。"""
    worker = _worker(candidate.device_id)
    if candidate.door_no in worker.busy:
        return
    worker.busy.add(candidate.door_no)
    worker.queue.put_nowait(_SettleJob(candidate=candidate, feature=feature, threshold_s=threshold_s))
    _ensure_worker_task(candidate.device_id)


def _enqueue_assign(job: _AssignJob) -> None:
    worker = _worker(job.device_id)
    if job.door_no in worker.busy:
        return
    worker.busy.add(job.door_no)
    worker.queue.put_nowait(job)
    _ensure_worker_task(job.device_id)


def _ensure_worker_task(device_id: str) -> None:
    worker = _worker(device_id)
    if worker.task is None or worker.task.done():
        worker.task = asyncio.create_task(_worker_loop(device_id))


async def _worker_loop(device_id: str) -> None:
    """一台设备的串行执行者：取一项跑一项，**之间不睡**。"""
    worker = _worker(device_id)
    while True:
        job = await worker.queue.get()
        door_no = job.door_no
        try:
            if isinstance(job, _AssignJob):
                await _perform_assign(job)
            else:
                await _perform_settle(job)
        except Exception as exc:  # noqa: BLE001 - 单次失败不得终止这台设备的处理
            _record("failed", "后台任务执行异常", job, level="ERROR", error=str(exc))
        finally:
            worker.busy.discard(door_no)
            worker.queue.task_done()
            # 跑完之后现态已经变了，重算这台设备的下一个到点
            reschedule(device_id)


# ── 执行 ──────────────────────────────────────────────────────────────────


async def _perform_settle(job: _SettleJob) -> None:
    """结算一次。失败即停，不重试。

    **走领域层**：`sessions.routes` 的 `perform_settle` 是"所有调用方共用"的那个入口，
    它自带前置检查；在这里另写一份就等于同一个判据有两个出处。
    """
    candidate = job.candidate
    feature = job.feature
    _attempted.add((candidate.device_id, candidate.session_id, feature))

    config = config_store.effective(candidate.device_id)
    still = still_allowed(candidate.cert_no, feature, config)
    if still is not None:
        _record("skipped", "下单前复查未通过，放弃这一次", job, reason=still)
        return

    password = password_of(config, feature, candidate.cert_no)
    if not password:
        # 名单里有他、却没有他的凭证：动作发不出去。属于配置漏项，要人看见。
        _record("no-password", "名单里有这个学号，但没有他的凭证", job, level="ERROR")
        return

    try:
        with session_scope() as session:
            result = await sessions_routes.perform_settle(
                session, candidate.device_id, SettleRequest(cert_no=candidate.cert_no, password=password)
            )
    except ApiProblemError as error:
        _report_problem(error, job)
        return

    if result.get("status") != outcomes.OK.value:
        _report_outcome(result, job)
        return

    _record(
        "settled",
        "续期已结算，等门空闲后补分配" if FEATURES_BY_KEY[feature].two_step else "自动结算已下发",
        job,
    )
    if not FEATURES_BY_KEY[feature].two_step:
        return

    # 分配的前置是服务端**先看见门空了**——`perform_assign` 自己要去读 door_live。门被释放
    # 必然出一帧，所以这里挂一条待办、由帧推动接着走，不轮询。
    _pending_assigns[(candidate.device_id, candidate.door_no)] = _PendingAssign(
        device_id=candidate.device_id,
        door_no=candidate.door_no,
        cert_no=candidate.cert_no,
        password=password,
        feature=feature,
        session_id=candidate.session_id,
        threshold_s=job.threshold_s,
        expires_at=_now_s() + ASSIGN_PENDING_TIMEOUT_S,
    )


async def _perform_assign(job: _AssignJob) -> None:
    """补上那一次分配。**只下一次**，不重试——它是否真成了，下一帧看得出来。"""
    key = (job.device_id, job.door_no)
    try:
        with session_scope() as session:
            result = await sessions_routes.perform_assign(
                session, job.device_id, AssignRequest(door_no=job.door_no, cert_no=job.cert_no, password=job.password)
            )
    except ApiProblemError as error:
        if error.problem.code in _WAIT_PROBLEMS:
            # 门还没空、或他名下还有未结束的会话：命令**根本没下发**，所以留着待办等下一帧
            # 不是"重试"——重试指的是把同一条命令再发一遍。
            _record("assign-waiting", "还轮不到分配，等下一帧", job, problem=error.problem.code)
            return
        _pending_assigns.pop(key, None)
        _report_problem(error, job)
        return

    _pending_assigns.pop(key, None)
    ok = result.get("status") == outcomes.OK.value
    _record(
        "assigned" if ok else "assign-failed",
        "续期已补分配" if ok else "续期补分配失败，请人工确认",
        job,
        level="INFO" if ok else "WARNING",
        status=str(result.get("status")),
        reason_code=str(result.get("reason_code") or "-"),
    )


def _report_problem(error: ApiProblemError, subject: _Auditable) -> None:
    """领域层的前置检查拒了。分清"现在还不该做"与"真出问题了"。

    前者（门还被占着、他还有会话、他已经走了）是正常情形，记 INFO 就够了；后者要人看见
    ——它意味着这次后台任务没做成，而喷头会一直占着，直到浴室端自己释放。
    """
    code = error.problem.code
    expected = code in _WAIT_PROBLEMS or code in _DONE_PROBLEMS
    _record(
        "not-applicable" if expected else "rejected",
        "现在还不该做，跳过" if expected else "领域层拒绝了这次后台任务，请人工确认",
        subject,
        level="INFO" if expected else "WARNING",
        problem=code,
        detail=error.detail,
    )


def _report_outcome(result: CommandOutcome, subject: _Auditable) -> None:
    """命令发出去了但没成。

    只认命令结果（见模块注释）——命令报 ok 也可能被浴室端拒了，而那种"没帮上忙"我们不
    追究。真失败的（设备半路卡住、超时）要人看见。
    """
    _record(
        "settle-failed",
        "结算失败，请人工确认",
        subject,
        level="WARNING",
        status=str(result.get("status")),
        reason_code=str(result.get("reason_code") or "-"),
        terminal_message=str(result.get("terminal_message") or ""),
    )


def _flush_pending_assign(device_id: str) -> None:
    """帧到了：该设备上那些待补的分配，够条件就补上。

    **这里不判"门空没空"**：那是 `perform_assign` 自己的前置（`_require_assignable_door`
    与 `_reject_duplicate_session`），在别处再写一遍就是同一个判据有两个出处。所以这里
    只管排进队列，前置满不满足由领域层说了算——它拒了就留着待办等下一帧（见
    `_perform_assign`），那时**命令根本没下发**，不算重试。

    待办也不在这里摘：摘除只在命令真下发、或明确放弃（下面那条过期分支）时做。
    """
    if not _running:
        return
    now = _now_s()

    for key in [k for k in _pending_assigns if k[0] == device_id]:
        waiting = _pending_assigns[key]
        if now > waiting.expires_at:
            _pending_assigns.pop(key, None)
            _record("assign-expired", "等门空闲超时，放弃这次分配", waiting, level="WARNING")
            continue

        _enqueue_assign(
            _AssignJob(
                device_id=device_id,
                door_no=waiting.door_no,
                cert_no=waiting.cert_no,
                password=waiting.password,
                feature=waiting.feature,
                session_id=waiting.session_id,
                threshold_s=waiting.threshold_s,
            )
        )


# ── 生命周期 ──────────────────────────────────────────────────────────────


def start() -> None:
    global _running
    _running = True
    log_event("INFO", "background-task.started", "后台任务已启动")


def stop() -> None:
    global _running
    _running = False
    for worker in _workers.values():
        for planned in worker.timers.values():
            planned.handle.cancel()
        worker.timers.clear()
        if worker.task is not None:
            worker.task.cancel()
            worker.task = None
    _workers.clear()
    _pending_assigns.clear()
    _attempted.clear()
    log_event("INFO", "background-task.stopped", "后台任务已停止")
