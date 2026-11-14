"""后台任务的调度与执行：定时器编排、复查、单次执行、串行队列、待补分配。

取数与命令下发都用 monkeypatch 换掉——这一层要验的是**编排**，不是 SQL 也不是协议。
每条都跑在真实的 asyncio 循环里，因为定时器与队列都是循环上的东西。

终局记录（`_record`）不在换掉之列：日志与审计表同路双写是它的全部意义，桩掉它就等于
换了被测对象。所以凡是走到终局的用例都带 `fresh_db`——审计行落进本用例的临时库；
conftest 的 `_isolate_default_db` 保证没带的用例碰不到仓库里的真实库。
"""

import asyncio
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests import device_link, door_link

from app.contracts import CommandOutcome
from app.core import device_clock, outcomes, problems
from app.core.clock import now_rfc3339, parse_rfc3339, rfc3339_ago, shift_rfc3339
from app.core.problems import ApiProblemError, Problem
from app.core.protocol import JsonObject
from app.db.session import session_scope
from app.models.enums import DoorState
from app.modules.devices import actions as device_actions
from app.modules.sessions import scheduler
from app.modules.sessions.decision import (
    JOB_AUTO_SETTLE,
    JOB_RENEW_SESSION,
    JOB_RENEW_WAIT,
    Candidate,
)
from app.modules.sessions.schemas import AssignRequest, SettleRequest
from app.persistence import audit as audit_repository


def _read_snapshots(device_id: str) -> list:
    """直读 _snapshots 的短会话包装：测试直调辅助。"""
    with session_scope() as session:
        return scheduler._snapshots(session, device_id)


SCHEDULER_PATH = Path(scheduler.__file__)

#: 这一层测的是**编排**，所以把时长上限放得极大：下面那些夹具动辄是"一小时前分配"，
#: 撞上真实默认值（900 秒）的话判据会先停手，测到的是"没动手"而不是"编排对不对"。
#: 时长上限本身由 test_decision.py 的专用用例盯。
CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": {"2026000001": "000000"},
    "renew_wait_threshold_s": 270,
    "renew_wait_max_duration_s": 86_400,
    "renew_session_enabled": True,
    "renew_session_students": {"2026000001": "000000"},
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": {"2026000001": "000000"},
    "auto_settle_pause_threshold_s": 60,
}

# 候选的时刻用「相对导入时刻」构造，**不写死绝对值**：预约续期的链上限（夹具 CONFIG
# 里的 24 小时）按真实时钟比较，写死的时刻跨过一天就会让上限判据把触发判没
# （2026-10-06 深夜实测：午夜后整批用例随钟点挂）。十分钟前 = 早已过点、必然排上，
# 又远在链上限之内。
_WAITING_AT = rfc3339_ago(600)

WAITING = Candidate(
    session_id=7,
    device_id="dev-1",
    door_no=3,
    cert_no="2026000001",
    state=DoorState.WAITING,
    assigned_at=_WAITING_AT,
    paused_at="",
    run_started_at=_WAITING_AT,
)


@pytest.fixture
def anchored() -> None:
    """给测试设备建立偏差记录。

    `door_live` 的三个时间字段（`open_time` / `in_time` / `close_time`）要经钟偏差换算，
    **没有记录时是空的**。真实设备连上后一个往返内就有（网关在 `link.hello` 之后立刻
    主动 ping），而且偏差跨重启持久化——不建立它，测的就不是线上的样子。
    """
    device_clock.load("dev-1", 0.0)


@pytest.fixture(autouse=True)
def _clean_scheduler() -> Iterator[None]:
    """每条之后清干净：这些是模块级状态，留着会串台。"""
    yield
    scheduler._workers.clear()
    scheduler._pending_assigns.clear()
    scheduler._attempted.clear()
    scheduler._running = False


def _seed_session(candidate: Candidate) -> None:
    """给桩出来的候选会话落真实行：审计行的 session_id 是真外键（usage_sessions）。

    id 显式给、与候选一致；登记设备满足外键；其余列给不参与判定的合法值。
    未声明库（该用例不落库）直接跳过——审计也不会发生，无需种子。
    """
    from datetime import datetime

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.models.usage import UsageSession
    from app.persistence import doors as doors_store

    with session_scope() as session:
        try:
            session.execute(text("SELECT 1 FROM devices LIMIT 1"))
        except OperationalError:
            return
    device_link.register_trusted_device(candidate.device_id)
    with session_scope() as session:
        assigned = datetime.fromisoformat(candidate.assigned_at.replace("Z", "+00:00"))
        if session.get(UsageSession, candidate.session_id) is None:
            session.add(
                UsageSession(
                    id=candidate.session_id,
                    device_id=doors_store._device_pk(session, candidate.device_id),
                    door_no=candidate.door_no,
                    cert_no=candidate.cert_no,
                    assigned_at=assigned,
                    assigned_source="observed",
                    last_seen_at=assigned,
                )
            )
            session.commit()


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[Candidate],
    sent: list[tuple[str, str, dict[str, object]]] | None = None,
) -> list[tuple[str, str, dict[str, object]]]:
    """把取数与**领域层的两个入口**换成可控的桩，返回记录下来的命令。

    这一层要验的是编排：定时器、队列、到点复查、单次执行。领域入口自身的真实性（前置
    检查、命令往返）不归这里管——那由 `test_job_runs.py`（真库 +
    真前置）与项目原有的 `test_sessions.py` 覆盖。所以这里连入口一起换掉，每条测试只
    验一层。
    """
    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(scheduler, "_snapshots", lambda _session, _device_id: candidates)
    # WAITING 无条件落种子：它只是桩（_snapshots 只认 candidates），但手动 _fire 它的
    # 用例（如"会话已没了"）的审计行同样要过真外键——FK 完整与判定桩是两件事。
    _seed_session(WAITING)
    for candidate in candidates:
        _seed_session(candidate)
    calls = sent if sent is not None else []

    async def fake_settle(_session: object, device_id: str, req: SettleRequest) -> CommandOutcome:
        calls.append((device_id, "session.settle", {"cert_no": req.cert_no, "password": req.password}))
        return CommandOutcome(status=outcomes.OK.value, reason_code="", terminal_message="", data=None)

    async def fake_assign(_session: object, device_id: str, req: AssignRequest) -> CommandOutcome:
        calls.append(
            (
                device_id,
                "session.assign",
                {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
            )
        )
        return CommandOutcome(status=outcomes.OK.value, reason_code="", terminal_message="", data=None)

    monkeypatch.setattr(scheduler.sessions_service, "perform_settle", fake_settle)
    monkeypatch.setattr(scheduler.sessions_service, "perform_assign", fake_assign)
    return calls


# ── 生命周期 ──


def test_start_and_stop_are_idempotent() -> None:
    async def body() -> None:
        scheduler.start()
        scheduler.start()
        assert scheduler._running is True

        await scheduler.stop()
        await scheduler.stop()
        assert scheduler._running is False

    asyncio.run(body())


def test_hooks_do_nothing_before_start() -> None:
    """没启动时不该建 worker、不该碰定时器。"""
    scheduler.on_frame("dev-1")
    scheduler.reload()
    scheduler.reschedule("dev-1")

    assert scheduler._workers == {}


# ── 定时器编排 ──


def test_each_door_gets_exactly_one_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """一个喷头只留一个定时器；已暂停的喷头由自动结算认领。"""

    async def body() -> None:
        scheduler.start()
        paused = Candidate(
            session_id=7,
            device_id="dev-1",
            door_no=3,
            cert_no="2026000001",
            state=DoorState.PAUSED,
            assigned_at="2026-10-05T11:00:00.000Z",
            paused_at="2026-10-05T11:00:00.000Z",
            run_started_at="2026-10-05T11:00:00.000Z",
        )
        _prepare(monkeypatch, [paused])
        scheduler.reschedule("dev-1")
        handles = scheduler._workers["dev-1"].timers

        assert list(handles) == [3]
        # 三个功能的状态互不相交，所以"已暂停"只有自动结算会认领
        with session_scope() as session:
            assert next(iter(scheduler._plan(session, "dev-1").values()))[2].job == JOB_AUTO_SETTLE

    asyncio.run(body())


def test_timer_is_dropped_when_the_session_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler.reschedule("dev-1")
        assert list(scheduler._workers["dev-1"].timers) == [3]

        _prepare(monkeypatch, [])  # 会话结束了
        scheduler.reschedule("dev-1")

        assert scheduler._workers["dev-1"].timers == {}

    asyncio.run(body())


def test_attempted_session_is_not_scheduled_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次执行的落点：动过手的会话不再排——失败也不重试。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._attempted.add((WAITING.device_id, WAITING.session_id, JOB_RENEW_WAIT))
        scheduler.reschedule("dev-1")

        assert scheduler._workers["dev-1"].timers == {}

    asyncio.run(body())


def test_closed_sessions_are_forgotten_without_touching_other_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话结束时清掉它的记录，否则集合一直长。

    而会话 id 是全库自增的，所以**只清本设备的**——否则 B 设备的帧会把 A 设备的记录一起
    清掉，那一场就会被重复动手。
    """
    monkeypatch.setattr(scheduler.doors_service, "open_sessions", lambda _session, _device_id: [])
    scheduler._attempted.add(("dev-1", 7, JOB_RENEW_WAIT))
    scheduler._attempted.add(("dev-2", 8, JOB_RENEW_WAIT))

    with session_scope() as session:
        scheduler._forget_closed_sessions(session, "dev-1")

    assert ("dev-1", 7, JOB_RENEW_WAIT) not in scheduler._attempted
    assert ("dev-2", 8, JOB_RENEW_WAIT) in scheduler._attempted


# ── 到点 ──


def test_fire_skips_when_recheck_fails(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """到点只说明该看一眼了；现态变了就放弃，而且**不告警**。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [])  # 会话没了
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(JOB_RENEW_WAIT, delay_s=0.0, threshold_s=270))

        assert scheduler._workers["dev-1"].queue.empty()

    asyncio.run(body())


def test_fire_enqueues_a_settle(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(JOB_RENEW_WAIT, delay_s=0.0, threshold_s=270))

        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert 3 in scheduler._workers["dev-1"].busy

    asyncio.run(body())


THRESHOLDS = {JOB_RENEW_WAIT: 270, JOB_RENEW_SESSION: 1140, JOB_AUTO_SETTLE: 60}


def _settle_job(job: str = JOB_RENEW_WAIT) -> scheduler._SettleJob:
    """`_record` 要的那套字段（见 `_Auditable`）——用结算待办当载体最省。"""
    return scheduler._SettleJob(candidate=WAITING, job=job, threshold_s=THRESHOLDS[job])


def _assign_job(door_no: int = 3) -> scheduler._AssignJob:
    """补分配待办。`session_id` 用 WAITING 那一场——续期两步共用它，查起来是一件事。"""
    return scheduler._AssignJob(
        device_id="dev-1",
        door_no=door_no,
        cert_no="2026000001",
        password="000000",
        job=JOB_RENEW_WAIT,
        session_id=WAITING.session_id,
        threshold_s=THRESHOLDS[JOB_RENEW_WAIT],
    )


def test_terminal_outcomes_are_recorded_on_one_path() -> None:
    """终局只能在 `_record` 里记：日志与落表是**一件事**，散开写迟早只做一半。

    这是源码级检查，不靠行为测试覆盖：行为测试只走得到那几条路径，而"新加一处终局却
    忘了落表"恰恰发生在没被覆盖的那条上。`started` / `stopped` 是生命周期、不是终局，
    所以它们直接写日志——这份白名单就是它们，多一个都要在这里说清楚。
    """
    source = SCHEDULER_PATH.read_text(encoding="utf-8")

    literals = sorted(re.findall(r'"job\.([a-z-]+)"', source))
    assert literals == ["started", "stopped"], f"还有别处直接写终局日志：{literals}"
    assert source.count("insert_job_run(") == 1, "落表必须只有一处"


def test_settle_lands_in_the_audit_table_with_its_own_fields(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """一次结算落一行，字段齐——审计表要能自己解释自己。

    重点在 `threshold_s`：配置没有历史，事后再去查"当时阈值多少"是查不到的，而那一行
    要能说清"为什么是这一刻"。
    """

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(_settle_job(JOB_RENEW_WAIT))

    asyncio.run(body())

    with session_scope() as session:
        rows = audit_repository.list_job_runs(session)
    assert [row["outcome"] for row in rows] == ["settled"]
    row = rows[0]
    assert row["job"] == JOB_RENEW_WAIT
    assert row["action"] == "session.settle"
    assert row["session_id"] == WAITING.session_id
    assert row["cert_no"] == WAITING.cert_no
    assert row["threshold_s"] == THRESHOLDS[JOB_RENEW_WAIT]
    assert row["reason"] is None  # 成了就有没理由要说


def test_a_rejected_settle_is_recorded_with_its_reason(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """领域层拒了也留一行，并带上理由——"没动手"同样是要留痕的事实。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])

        async def reject(_session: object, _device_id: str, _req: object) -> object:
            raise ApiProblemError(problems.NO_OCCUPIED_DOOR, "已经没有占用中的喷头了")

        monkeypatch.setattr(scheduler.sessions_service, "perform_settle", reject)
        await scheduler._perform_settle(_settle_job(JOB_AUTO_SETTLE))

    asyncio.run(body())

    with session_scope() as session:
        rows = audit_repository.list_job_runs(session)
    assert [row["outcome"] for row in rows] == ["not-applicable"]
    assert rows[0]["job"] == JOB_AUTO_SETTLE
    assert rows[0]["action"] == "session.settle"
    assert rows[0]["threshold_s"] == THRESHOLDS[JOB_AUTO_SETTLE]


def test_same_door_is_not_queued_twice(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._enqueue_settle(WAITING, JOB_RENEW_WAIT, THRESHOLDS[JOB_RENEW_WAIT])
        scheduler._enqueue_settle(WAITING, JOB_RENEW_WAIT, THRESHOLDS[JOB_RENEW_WAIT])

        assert scheduler._workers["dev-1"].queue.qsize() == 1

    asyncio.run(body())


# ── 执行 ──


def test_settle_stops_when_there_is_no_password(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """名单里有他却没有他的凭证：发不出去，也不能假装发了。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(
            scheduler.config_store,
            "effective",
            lambda _session, _device_id: {**CONFIG, "renew_wait_students": {}},
        )
        await scheduler._perform_settle(_settle_job(JOB_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_renewal_arms_a_pending_assign_but_settle_does_not(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """续期类要在结算之后再分配；自动结算只要前半步。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(_settle_job(JOB_RENEW_WAIT))
        assert ("dev-1", 3) in scheduler._pending_assigns

        scheduler._pending_assigns.clear()
        await scheduler._perform_settle(_settle_job(JOB_AUTO_SETTLE))
        assert scheduler._pending_assigns == {}

    asyncio.run(body())


def test_settle_records_the_attempt_before_sending(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """先记后发：否则失败之后重算出的到点还是同一刻，会自旋。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(_settle_job(JOB_RENEW_WAIT))

        assert (WAITING.device_id, WAITING.session_id, JOB_RENEW_WAIT) in scheduler._attempted

    asyncio.run(body())


def test_settle_stops_when_the_roster_changed_underneath(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """复查到下单之间隔着取数与排队，所以下单前要再问一次名单。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(
            scheduler.config_store,
            "effective",
            lambda _session, _device_id: {**CONFIG, "renew_wait_enabled": False},
        )
        await scheduler._perform_settle(_settle_job(JOB_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_domain_rejections_are_split_into_normal_and_noteworthy(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """领域层拒了要分清两类：门还被占着/他还有会话/他已经走了是正常，"设备离线"之类不是。

    分错的两个方向都坏：把正常当失败会刷满告警，把失败当正常就没人知道这次的维护没做成。
    所以这条**验日志级别**，而不是重述那两个集合——后者会因为错误的原因通过。

    日志观测改用依赖替换而非全局 logger 钩子，依据：
    - pytest 官方文档「How to use the monkeypatch fixture」：monkeypatch 是官方
      测试工具，替换依赖对象后测试、用例结束自动还原（docs.pytest.org 的
      monkeypatch 章节）。
    - xUnit Test Patterns（Gerard Meszaros）的 Test Stub：观察点是被测代码对
      依赖（log_event）的调用，而非全局 logging 状态——后者与测试执行顺序耦合。
    - 实测证据：caplog（挂根 logger）与 assertLogs（替换目标 logger handlers）
      都是官方工具，但与本应用 lifespan 里的 configure_logging（重建 handlers、
      propagate=False）相互干扰，在并行与 CI 上实测丢记录；依赖替换不触碰
      全局 logging 状态，CI 实测通过。
    """
    _seed_session(WAITING)  # 审计行的 session_id 是真外键

    events: list[tuple[str, str]] = []
    monkeypatch.setattr(scheduler, "log_event", lambda level, event, *_a, **_kw: events.append((level, event)))

    def problem(code: str) -> ApiProblemError:
        return ApiProblemError(Problem(code, 409, "x"), "细节")

    normal = (problems.DOOR_NOT_FREE.code, problems.DUPLICATE_SESSION.code, problems.NO_OCCUPIED_DOOR.code)
    for code in normal:
        scheduler._report_problem(problem(code), _settle_job(JOB_AUTO_SETTLE))
    assert [level for level, _event in events] == ["INFO", "INFO", "INFO"]

    events.clear()
    scheduler._report_problem(problem(problems.DEVICE_OFFLINE.code), _settle_job(JOB_AUTO_SETTLE))
    assert [level for level, _event in events] == ["WARNING"]


def test_assign_is_sent_exactly_once(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """单次执行：失败也不重发。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [])
        await scheduler._perform_assign(_assign_job(3))

        assert len(calls) == 1
        assert calls[0][1] == "session.assign"

    asyncio.run(body())


# ── 待补分配 ──


def _arm(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    scheduler.start()
    _prepare(monkeypatch, [])
    _seed_session(WAITING)
    fields: dict[str, object] = {
        "device_id": "dev-1",
        "door_no": 3,
        "cert_no": "2026000001",
        "password": "000000",
        "job": JOB_RENEW_WAIT,
        "session_id": WAITING.session_id,
        "threshold_s": THRESHOLDS[JOB_RENEW_WAIT],
        "expires_at": 1e9,
    }
    fields.update(overrides)
    scheduler._pending_assigns[("dev-1", 3)] = scheduler._PendingAssign(**fields)  # type: ignore[arg-type]


def test_pending_assign_is_handed_to_the_domain_layer(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """帧到了就把待补的分配交给领域层——"门空没空"由它判，这里不再自写一遍判据。

    所以即使门还占着，队列里也会有一项：那是"去试一次"，而试的结果由领域层的前置给。
    它拒了也不丢待办（见 [test_a_wait_rejection_keeps_the_pending_assign]）。
    """

    async def body() -> None:
        _arm(monkeypatch)
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) in scheduler._pending_assigns
        assert scheduler._workers["dev-1"].queue.qsize() == 1

    asyncio.run(body())


def test_a_wait_rejection_keeps_the_pending_assign(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """领域层说"还轮不到"（门还占着、或他名下还有会话）→ 留着待办等下一帧。

    命令根本没下发，所以留待办不是"重试"——重试指的是把同一条命令再发一遍。
    """

    async def body() -> None:
        _arm(monkeypatch)

        async def reject(_session: object, _device_id: str, _req: object) -> object:
            raise ApiProblemError(problems.DOOR_NOT_FREE, "还被占着")

        monkeypatch.setattr(scheduler.sessions_service, "perform_assign", reject)
        await scheduler._perform_assign(_assign_job(3))

        assert ("dev-1", 3) in scheduler._pending_assigns

    asyncio.run(body())


def test_any_other_rejection_drops_the_pending_assign(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """其它拒绝（设备离线、通道积压……）→ 放弃这次分配。

    单次执行的边界就在这里：等下一帧是给"还轮不到"的，不是给"发失败了"的。
    """

    async def body() -> None:
        _arm(monkeypatch)

        async def reject(_session: object, _device_id: str, _req: object) -> object:
            raise ApiProblemError(problems.DEVICE_OFFLINE, "设备离线")

        monkeypatch.setattr(scheduler.sessions_service, "perform_assign", reject)
        await scheduler._perform_assign(_assign_job(3))

        assert ("dev-1", 3) not in scheduler._pending_assigns

    asyncio.run(body())


def test_pending_assign_fires_once_the_door_is_free(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """够条件就把分配排进队列。

    **待办这时还不摘**：摘除只在命令真下发（或明确放弃）时做——万一真下发时前置又变了，
    留着待办还能等下一帧。
    """

    async def body() -> None:
        _arm(monkeypatch)
        scheduler._flush_pending_assign("dev-1")

        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert ("dev-1", 3) in scheduler._pending_assigns

    asyncio.run(body())


def test_pending_assign_expires(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """没有这个时限，设备离线几小时再上线会给一个早已走掉的学生开一场会话。"""

    async def body() -> None:
        _arm(monkeypatch, expires_at=0.0)
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) not in scheduler._pending_assigns

    asyncio.run(body())


# ── 串行 ──


def test_each_device_has_its_own_worker(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """串行的边界是设备：两台设备各排各的，互相不等。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        other = replace(WAITING, device_id="dev-2", door_no=1)
        scheduler._enqueue_settle(WAITING, JOB_RENEW_WAIT, THRESHOLDS[JOB_RENEW_WAIT])
        scheduler._enqueue_settle(other, JOB_AUTO_SETTLE, THRESHOLDS[JOB_AUTO_SETTLE])

        assert set(scheduler._workers) == {"dev-1", "dev-2"}
        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert scheduler._workers["dev-2"].queue.qsize() == 1
        assert scheduler._workers["dev-1"].task is not scheduler._workers["dev-2"].task

    asyncio.run(body())


def test_worker_runs_jobs_back_to_back(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """队列之间不睡：取一项跑一项。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [])
        scheduler.start()
        for door_no in (3, 4):
            scheduler._enqueue_assign(_assign_job(door_no))
        await asyncio.wait_for(scheduler._workers["dev-1"].queue.join(), timeout=3)

        assert [call[2]["door_no"] for call in calls] == [3, 4]

    asyncio.run(body())


def test_worker_survives_a_failing_job(monkeypatch: pytest.MonkeyPatch, fresh_db: str) -> None:
    """单次失败只该带走那一项，不该终止这台设备的处理。"""

    async def body() -> None:
        _prepare(monkeypatch, [])
        scheduler.start()

        message = "设备端炸了"

        async def boom(_job: object) -> None:
            raise RuntimeError(message)

        monkeypatch.setattr(scheduler, "_perform_settle", boom)
        scheduler._enqueue_settle(WAITING, JOB_RENEW_WAIT, THRESHOLDS[JOB_RENEW_WAIT])
        await asyncio.wait_for(scheduler._workers["dev-1"].queue.join(), timeout=3)

        assert scheduler._workers["dev-1"].busy == set()
        assert scheduler._workers["dev-1"].task is not None
        assert not scheduler._workers["dev-1"].task.done()

    asyncio.run(body())


def test_reload_reschedules_every_known_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置保存即生效：所有已经建过 worker 的设备都要按新值重算。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        seen: list[str] = []
        monkeypatch.setattr(scheduler, "reschedule", seen.append)
        scheduler._worker("dev-1")
        scheduler._worker("dev-2")

        scheduler.reload()

        assert sorted(seen) == ["dev-1", "dev-2"]

    asyncio.run(body())


def test_stop_cancels_timers_and_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """停机要把挂着的定时器与常驻任务都收掉，否则重启用例里它们会串台。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler.reschedule("dev-1")
        scheduler._enqueue_settle(WAITING, JOB_RENEW_WAIT, THRESHOLDS[JOB_RENEW_WAIT])
        worker = scheduler._workers["dev-1"]
        assert worker.timers
        assert worker.task is not None

        await scheduler.stop()

        assert worker.timers == {}
        assert worker.task is None
        assert scheduler._workers == {}
        assert scheduler._pending_assigns == {}

    asyncio.run(body())


# ── 取数（真实库）──


def _frame(captured_at: str = "2026-10-05T11:00:00.000Z", **door: object) -> JsonObject:
    """一帧：只填判据用得上的那几个读数。

    `captured_at` 必须与门上报的时刻一起推进——`_assigned_at` 有一条"起点不晚于本帧"
    的自洽检验，帧的采集时刻不跟着走，新分配的起点就会被它拒掉、退回本帧时刻。
    """
    return {
        "snapshot_id": uuid.uuid4().hex,
        "source": "db_watch",
        "captured_at": captured_at,
        "doors": [{"door_no": 3, "cert_no": "2026000001", **door}],
    }


def test_snapshots_reads_only_the_derived_layer(fresh_db: str) -> None:
    """取数只走 `usage_session` + `door_live`，原始帧一概不读。"""
    door_link.feed("dev-1", _frame(card_no=1001, open_time="2026-10-05T11:00:00.000Z"))

    found = _read_snapshots("dev-1")

    assert len(found) == 1
    assert found[0].state is DoorState.WAITING
    assert found[0].door_no == 3
    assert found[0].cert_no == "2026000001"
    assert found[0].assigned_at == "2026-10-05T11:00:00.000Z"
    assert found[0].run_started_at == "2026-10-05T11:00:00.000Z"
    assert found[0].session_id > 0


def test_snapshots_skips_a_session_without_a_live_row(fresh_db: str) -> None:
    """会话还在、门的现态却没了：不判，等下一帧。"""
    door_link.feed("dev-1", _frame(card_no=1001, open_time="2026-10-05T11:00:00.000Z"))
    door_link.clear_live("dev-1")

    assert _read_snapshots("dev-1") == []


def test_run_started_at_is_the_first_session_of_the_run(fresh_db: str) -> None:
    """这一串的起点是学生自己那一次分配——续出来的每一场都不该把它往后挪。"""
    door_link.feed(
        "dev-1", _frame(captured_at="2026-10-05T11:00:00.000Z", card_no=1001, open_time="2026-10-05T11:00:00.000Z")
    )
    assert _read_snapshots("dev-1")[0].run_started_at == "2026-10-05T11:00:00.000Z"

    # 我们自己续出来的那一场：门空了、紧接着又被分配回同一个喷头
    door_link.feed("dev-1", _frame(captured_at="2026-10-05T11:05:00.000Z"))
    door_link.feed(
        "dev-1", _frame(captured_at="2026-10-05T11:05:04.000Z", card_no=1001, open_time="2026-10-05T11:05:04.000Z")
    )

    # 换了一场，起点仍是第一场——时长上限从第一场算起，挪了它这个上限就永远到不了
    assert _read_snapshots("dev-1")[0].run_started_at == "2026-10-05T11:00:00.000Z"


def test_run_started_at_moves_after_a_real_break(fresh_db: str) -> None:
    """喷头空着超过 300 秒，说明那个人的占用真的结束了——计时从头开始。"""
    door_link.feed(
        "dev-1", _frame(captured_at="2026-10-05T11:00:00.000Z", card_no=1001, open_time="2026-10-05T11:00:00.000Z")
    )
    door_link.feed("dev-1", _frame(captured_at="2026-10-05T11:05:00.000Z"))
    # 中间空了一小时才被重新分配
    door_link.feed(
        "dev-1", _frame(captured_at="2026-10-05T12:05:00.000Z", card_no=1001, open_time="2026-10-05T12:05:00.000Z")
    )

    assert _read_snapshots("dev-1")[0].run_started_at == "2026-10-05T12:05:00.000Z"


# ── 接缝：一帧进来，到命令出、再到补上分配 ────────────────────────────────


def _ago(seconds: int) -> str:
    """相对当前时刻往前推。

    "过了多久"是拿**服务端当下时刻**算的，所以写死日期会在机器时钟不同的时候变成
    "未来"——那时到点还早，等不到命令出去。
    """
    past = shift_rfc3339(now_rfc3339(), -seconds)
    assert past is not None
    return past


async def _let_the_loop_run(ticks: int = 20) -> None:
    """让循环转几圈，到点回调与 worker 才有机会跑。

    **不能只 `await queue.join()`**：队列空时它立刻返回、一次都不让出控制权，`call_at`
    排上的回调根本轮不到执行。到点是"过去某一刻"，所以第一圈就该跑起来。
    """
    for _ in range(ticks):
        await asyncio.sleep(0)


def test_a_frame_ends_in_a_settle_then_an_assign(
    fresh_db: str, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """全链路：帧 → 挂到点 → 到点复查 → 结算 → 门空了的那一帧 → 补分配。

    这是判据与机制之间的接缝：分段测过之后，还要证明它们接起来真的会把命令发出去。
    取数与前置检查都走真实库（领域层的两个入口也真跑），只有命令下发换成可控的桩。

    设备必须**已信任且在线**：领域层第一句就是那个守卫，缺了它这一场会以"设备没被信任"
    结束，而断言看到的却是"什么都没发生"——那等于把测试测假了。
    """
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(device_actions, "send_command_and_wait", fake_send)

    # 一小时前分配、一直没开阀：预约续期的 270 秒早已过点
    door_link.feed("dev-1", _frame(captured_at=_ago(3600), card_no=1001, open_time=_ago(3600)))

    async def body() -> None:
        session_id = _read_snapshots("dev-1")[0].session_id
        scheduler.start()
        scheduler.on_frame("dev-1")  # 网关的挂钩点
        await _let_the_loop_run()

        # 结算发出去了，并且挂上了"等门空闲再补分配"
        assert [action for action, _params in calls] == ["session.settle"]
        assert calls[0][1] == {"cert_no": "2026000001", "password": "000000"}
        assert ("dev-1", 3) in scheduler._pending_assigns
        assert ("dev-1", session_id, JOB_RENEW_WAIT) in scheduler._attempted

        # 门被释放的那一帧：会话收尾，待分配接上
        door_link.feed("dev-1", _frame(captured_at=_ago(3000)))
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle", "session.assign"]
        assert calls[1][1] == {"door_no": 3, "cert_no": "2026000001", "password": "000000"}
        assert scheduler._pending_assigns == {}

    with device_link.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_the_same_session_is_settled_only_once(
    fresh_db: str, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次执行：帧再来几次也不会为同一个会话再下一单。"""
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(device_actions, "send_command_and_wait", fake_send)
    door_link.feed("dev-1", _frame(captured_at=_ago(3600), card_no=1001, open_time=_ago(3600)))

    async def body() -> None:
        scheduler.start()
        for _ in range(3):
            scheduler.on_frame("dev-1")
            await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]

    with device_link.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_auto_settle_only_settles_and_never_reassigns(
    fresh_db: str, anchored: None, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动结算只做前半步：结完就完了，不补分配。

    会话续期只在淋浴中适用，而这一场已经关了阀两分钟（超过 60 秒的阈值）——所以动手的是
    自动结算。暂停时刻取自 `door_live.close_time`，所以这台设备要有偏差记录。
    """
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(device_actions, "send_command_and_wait", fake_send)
    door_link.feed(
        "dev-1", _frame(captured_at=_ago(120), card_no=1001, open_time=_ago(300), close_time=_ago(120), bath_time_s=120)
    )

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]
        assert scheduler._pending_assigns == {}

    with device_link.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_a_student_outside_the_roster_is_never_touched(
    fresh_db: str, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """名单是"允许对谁动手"的唯一出处：不在名单里，一帧下来什么都不发生。

    设备在这里也要**已信任且在线**——否则"什么都没发生"可能只是因为设备没被信任，
    那这条测试就测了个空。
    """
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(device_actions, "send_command_and_wait", fake_send)
    door_link.feed("dev-1", _frame(captured_at=_ago(3600), card_no=1001, cert_no="2026000006", open_time=_ago(3600)))

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")

        assert calls == []
        assert scheduler._workers["dev-1"].timers == {}

    with device_link.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_snapshot_reports_scheduled_timers(fresh_db: str) -> None:
    """只读读出：把内存里的定时器报成可显示的行，到点换算成服务端 RFC 3339。

    这是概览页后台任务卡"已排定的动作"的来源——判据仍只在 reschedule 一处，
    读数不做任何判断。
    """
    candidate = Candidate(
        session_id=1,
        device_id="dev-snap-status",
        door_no=3,
        cert_no="2026000001",
        state=DoorState.BATHING,
        assigned_at="2026-10-05T12:00:00.000Z",
        paused_at="",
        run_started_at="2026-10-05T12:00:00.000Z",
    )
    trigger = scheduler.Trigger(job="renew_session", delay_s=5.0, threshold_s=1140)

    async def body() -> None:
        loop = asyncio.get_running_loop()
        worker = scheduler._worker("dev-snap-status")
        worker.timers[3] = scheduler._Planned(
            at=time.monotonic() + 5.0,
            candidate=candidate,
            trigger=trigger,
            handle=loop.call_later(5.0, lambda: None),
        )
        try:
            with session_scope() as session:
                rows = scheduler.snapshot(session)
        finally:
            worker.timers.clear()
        assert rows == [
            {
                "device_id": "dev-snap-status",
                "door_no": 3,
                "cert_no": "2026000001",
                "job": "renew_session",
                "at": rows[0]["at"],
                "state": "bathing",
                "session_id": 1,
                "started_at": "2026-10-05T12:00:00.000Z",
                "threshold_s": 1140,
                "run_started_at": "2026-10-05T12:00:00.000Z",
                "max_duration_s": None,
            }
        ]
        assert parse_rfc3339(str(rows[0]["at"])) is not None

    asyncio.run(body())
