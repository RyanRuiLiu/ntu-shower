"""后台任务的调度与执行：定时器编排、复查、单次执行、串行队列、待补分配。

取数与命令下发都用 monkeypatch 换掉——这一层要验的是**编排**，不是 SQL 也不是协议。
每条都跑在真实的 asyncio 循环里，因为定时器与队列都是循环上的东西。
"""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import device_clock, outcomes, problems
from app.core.clock import now_rfc3339, shift_rfc3339
from app.core.db import get_conn
from app.core.problems import ApiProblemError, Problem
from app.core.protocol import JsonObject
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState
from app.modules.sessions import scheduler
from app.modules.sessions import service as sessions_service
from app.modules.sessions.decision import (
    FEATURE_AUTO_SETTLE,
    FEATURE_RENEW_SESSION,
    FEATURE_RENEW_WAIT,
    Candidate,
)
from app.modules.sessions.schemas import AssignRequest, CommandOutcome, SettleRequest

CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": {"99990052": "000000"},
    "renew_wait_threshold_s": 270,
    "renew_wait_max_count": 3,
    "renew_session_enabled": True,
    "renew_session_students": {"99990052": "000000"},
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": {"99990052": "000000"},
    "auto_settle_pause_threshold_s": 60,
}

WAITING = Candidate(
    session_id=7,
    device_id="dev-1",
    door_no=3,
    cert_no="99990052",
    state=DoorState.WAITING,
    assigned_at="2026-10-05T11:00:00.000Z",  # 早已过点：必然排上
    paused_at="",
    renew_count=0,
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


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    candidates: list[Candidate],
    live: dict[int, JsonObject] | None = None,
    active_by_cert: object = None,
    sent: list[tuple[str, str, dict[str, object]]] | None = None,
) -> list[tuple[str, str, dict[str, object]]]:
    """把取数与**领域层的两个入口**换成可控的桩，返回记录下来的命令。

    这一层要验的是编排：定时器、队列、到点复查、单次执行。领域入口自身的真实性（前置
    检查、命令往返）不归这里管——那由 `test_background_tasks_real_data.py`（真库 +
    真前置）与项目原有的 `test_sessions.py` 覆盖。所以这里连入口一起换掉，每条测试只
    验一层。
    """
    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(scheduler, "_snapshots", lambda _device_id: candidates)
    monkeypatch.setattr(
        scheduler.doors_repository,
        "get_all_live",
        lambda _device_id: live if live is not None else {3: {"state": "free", "close_time": ""}},
    )
    monkeypatch.setattr(scheduler.doors_repository, "get_active_by_cert", lambda _device_id, _cert: active_by_cert)
    calls = sent if sent is not None else []

    async def fake_settle(device_id: str, req: SettleRequest) -> CommandOutcome:
        calls.append((device_id, "session.settle", {"cert_no": req.cert_no, "password": req.password}))
        return CommandOutcome(status=outcomes.OK.value, reason_code="", terminal_message="", data=None)

    async def fake_assign(device_id: str, req: AssignRequest) -> CommandOutcome:
        calls.append(
            (
                device_id,
                "session.assign",
                {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
            )
        )
        return CommandOutcome(status=outcomes.OK.value, reason_code="", terminal_message="", data=None)

    monkeypatch.setattr(scheduler.sessions_routes, "perform_settle", fake_settle)
    monkeypatch.setattr(scheduler.sessions_routes, "perform_assign", fake_assign)
    return calls


# ── 生命周期 ──


def test_start_and_stop_are_idempotent() -> None:
    scheduler.start()
    scheduler.start()
    assert scheduler._running is True

    scheduler.stop()
    scheduler.stop()
    assert scheduler._running is False


def test_hooks_do_nothing_before_start() -> None:
    """没启动时不该建 worker、不该碰定时器。"""
    scheduler.on_frame("dev-1")
    scheduler.reload()
    scheduler.reschedule("dev-1")

    assert scheduler._workers == {}


# ── 定时器编排 ──


def test_each_door_gets_exactly_one_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    """一个喷头只留一个定时器；同一喷头两条判据时留最早的那条。"""

    async def body() -> None:
        scheduler.start()
        paused = Candidate(
            session_id=7,
            device_id="dev-1",
            door_no=3,
            cert_no="99990052",
            state=DoorState.PAUSED,
            assigned_at="2026-10-05T11:00:00.000Z",
            paused_at="2026-10-05T11:00:00.000Z",
            renew_count=0,
        )
        _prepare(monkeypatch, [paused])
        scheduler.reschedule("dev-1")
        handles = scheduler._workers["dev-1"].timers

        assert list(handles) == [3]
        # 同一喷头两条判据都已过点，留最早的那条：关阀比分配晚得多，自动结算先到点
        assert next(iter(scheduler._plan("dev-1").values()))[2].feature == FEATURE_AUTO_SETTLE

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
        scheduler._attempted.add((WAITING.device_id, WAITING.session_id, FEATURE_RENEW_WAIT))
        scheduler.reschedule("dev-1")

        assert scheduler._workers["dev-1"].timers == {}

    asyncio.run(body())


def test_closed_sessions_are_forgotten_without_touching_other_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话结束时清掉它的记录，否则集合一直长。

    而会话 id 是全库自增的，所以**只清本设备的**——否则 B 设备的帧会把 A 设备的记录一起
    清掉，那一场就会被重复动手。
    """
    monkeypatch.setattr(scheduler.doors_repository, "list_open_sessions_of", lambda _device_id: [])
    scheduler._attempted.add(("dev-1", 7, FEATURE_RENEW_WAIT))
    scheduler._attempted.add(("dev-2", 8, FEATURE_RENEW_WAIT))

    scheduler._forget_closed_sessions("dev-1")

    assert ("dev-1", 7, FEATURE_RENEW_WAIT) not in scheduler._attempted
    assert ("dev-2", 8, FEATURE_RENEW_WAIT) in scheduler._attempted


# ── 到点 ──


def test_fire_skips_when_recheck_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """到点只说明该看一眼了；现态变了就放弃，而且**不告警**。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [])  # 会话没了
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(FEATURE_RENEW_WAIT, delay_s=0.0, threshold_s=270))

        assert scheduler._workers["dev-1"].queue.empty()

    asyncio.run(body())


def test_fire_enqueues_a_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(FEATURE_RENEW_WAIT, delay_s=0.0, threshold_s=270))

        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert 3 in scheduler._workers["dev-1"].busy

    asyncio.run(body())


THRESHOLDS = {FEATURE_RENEW_WAIT: 270, FEATURE_RENEW_SESSION: 1140, FEATURE_AUTO_SETTLE: 60}


def _settle_job(feature: str = FEATURE_RENEW_WAIT) -> scheduler._SettleJob:
    """`_record` 要的那套字段（见 `_Auditable`）——用结算待办当载体最省。"""
    return scheduler._SettleJob(candidate=WAITING, feature=feature, threshold_s=THRESHOLDS[feature])


def _assign_job(door_no: int = 3) -> scheduler._AssignJob:
    """补分配待办。`session_id` 用 WAITING 那一场——续期两步共用它，查起来是一件事。"""
    return scheduler._AssignJob(
        device_id="dev-1",
        door_no=door_no,
        cert_no="99990052",
        password="000000",
        feature=FEATURE_RENEW_WAIT,
        session_id=WAITING.session_id,
        threshold_s=THRESHOLDS[FEATURE_RENEW_WAIT],
    )


def test_same_door_is_not_queued_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT, THRESHOLDS[FEATURE_RENEW_WAIT])
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT, THRESHOLDS[FEATURE_RENEW_WAIT])

        assert scheduler._workers["dev-1"].queue.qsize() == 1

    asyncio.run(body())


# ── 执行 ──


def test_settle_stops_when_there_is_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """名单里有他却没有他的凭证：发不出去，也不能假装发了。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(
            scheduler.config_store, "effective", lambda _device_id: {**CONFIG, "renew_wait_students": {}}
        )
        await scheduler._perform_settle(_settle_job(FEATURE_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_renewal_arms_a_pending_assign_but_settle_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """续期类要在结算之后再分配；自动结算只要前半步。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(_settle_job(FEATURE_RENEW_WAIT))
        assert ("dev-1", 3) in scheduler._pending_assigns

        scheduler._pending_assigns.clear()
        await scheduler._perform_settle(_settle_job(FEATURE_AUTO_SETTLE))
        assert scheduler._pending_assigns == {}

    asyncio.run(body())


def test_settle_records_the_attempt_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """先记后发：否则失败之后重算出的到点还是同一刻，会自旋。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(_settle_job(FEATURE_RENEW_WAIT))

        assert (WAITING.device_id, WAITING.session_id, FEATURE_RENEW_WAIT) in scheduler._attempted

    asyncio.run(body())


def test_settle_stops_when_the_roster_changed_underneath(monkeypatch: pytest.MonkeyPatch) -> None:
    """复查到下单之间隔着取数与排队，所以下单前要再问一次名单。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(
            scheduler.config_store, "effective", lambda _device_id: {**CONFIG, "renew_wait_enabled": False}
        )
        await scheduler._perform_settle(_settle_job(FEATURE_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_domain_rejections_are_split_into_normal_and_noteworthy(caplog: pytest.LogCaptureFixture) -> None:
    """领域层拒了要分清两类：门还被占着/他还有会话/他已经走了是正常，"设备离线"之类不是。

    分错的两个方向都坏：把正常当失败会刷满告警，把失败当正常就没人知道这次的维护没做成。
    所以这条**验日志级别**，而不是重述那两个集合——后者会因为错误的原因通过。
    """

    def problem(code: str) -> ApiProblemError:
        return ApiProblemError(Problem(code, 409, "x"), "细节")

    normal = (problems.DOOR_NOT_FREE.code, problems.DUPLICATE_SESSION.code, problems.NO_OCCUPIED_DOOR.code)
    for code in normal:
        caplog.clear()
        with caplog.at_level("INFO"):
            scheduler._report_problem(problem(code), _settle_job(FEATURE_AUTO_SETTLE))
        assert [record.levelname for record in caplog.records] == ["INFO"], code

    caplog.clear()
    with caplog.at_level("INFO"):
        scheduler._report_problem(problem(problems.DEVICE_OFFLINE.code), _settle_job(FEATURE_AUTO_SETTLE))
    assert [record.levelname for record in caplog.records] == ["WARNING"]


def test_assign_is_sent_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次执行：失败也不重发。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [])
        await scheduler._perform_assign(_assign_job(3))

        assert len(calls) == 1
        assert calls[0][1] == "session.assign"

    asyncio.run(body())


# ── 待补分配 ──


def _arm(
    monkeypatch: pytest.MonkeyPatch,
    live: dict[int, JsonObject] | None = None,
    active_by_cert: object = None,
    **overrides: object,
) -> None:
    scheduler.start()
    _prepare(monkeypatch, [], live=live, active_by_cert=active_by_cert)
    fields: dict[str, object] = {
        "device_id": "dev-1",
        "door_no": 3,
        "cert_no": "99990052",
        "password": "000000",
        "feature": FEATURE_RENEW_WAIT,
        "session_id": WAITING.session_id,
        "threshold_s": THRESHOLDS[FEATURE_RENEW_WAIT],
        "expires_at": 1e9,
    }
    fields.update(overrides)
    scheduler._pending_assigns[("dev-1", 3)] = scheduler._PendingAssign(**fields)  # type: ignore[arg-type]


def test_pending_assign_is_handed_to_the_domain_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """帧到了就把待补的分配交给领域层——"门空没空"由它判，这里不再自写一遍判据。

    所以即使门还占着，队列里也会有一项：那是"去试一次"，而试的结果由领域层的前置给。
    它拒了也不丢待办（见 [test_a_wait_rejection_keeps_the_pending_assign]）。
    """

    async def body() -> None:
        _arm(monkeypatch, live={3: {"state": "bathing", "close_time": ""}})
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) in scheduler._pending_assigns
        assert scheduler._workers["dev-1"].queue.qsize() == 1

    asyncio.run(body())


def test_a_wait_rejection_keeps_the_pending_assign(monkeypatch: pytest.MonkeyPatch) -> None:
    """领域层说"还轮不到"（门还占着、或他名下还有会话）→ 留着待办等下一帧。

    命令根本没下发，所以留待办不是"重试"——重试指的是把同一条命令再发一遍。
    """

    async def body() -> None:
        _arm(monkeypatch)

        async def reject(_device_id: str, _req: object) -> object:
            raise ApiProblemError(problems.DOOR_NOT_FREE, "还被占着")

        monkeypatch.setattr(scheduler.sessions_routes, "perform_assign", reject)
        await scheduler._perform_assign(_assign_job(3))

        assert ("dev-1", 3) in scheduler._pending_assigns

    asyncio.run(body())


def test_any_other_rejection_drops_the_pending_assign(monkeypatch: pytest.MonkeyPatch) -> None:
    """其它拒绝（设备离线、通道积压……）→ 放弃这次分配。

    单次执行的边界就在这里：等下一帧是给"还轮不到"的，不是给"发失败了"的。
    """

    async def body() -> None:
        _arm(monkeypatch)

        async def reject(_device_id: str, _req: object) -> object:
            raise ApiProblemError(problems.DEVICE_OFFLINE, "设备离线")

        monkeypatch.setattr(scheduler.sessions_routes, "perform_assign", reject)
        await scheduler._perform_assign(_assign_job(3))

        assert ("dev-1", 3) not in scheduler._pending_assigns

    asyncio.run(body())


def test_pending_assign_fires_once_the_door_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_pending_assign_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有这个时限，设备离线几小时再上线会给一个早已走掉的学生开一场会话。"""

    async def body() -> None:
        _arm(monkeypatch, expires_at=0.0)
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) not in scheduler._pending_assigns

    asyncio.run(body())


# ── 串行 ──


def test_each_device_has_its_own_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """串行的边界是设备：两台设备各排各的，互相不等。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        other = replace(WAITING, device_id="dev-2", door_no=1)
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT, THRESHOLDS[FEATURE_RENEW_WAIT])
        scheduler._enqueue_settle(other, FEATURE_AUTO_SETTLE, THRESHOLDS[FEATURE_AUTO_SETTLE])

        assert set(scheduler._workers) == {"dev-1", "dev-2"}
        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert scheduler._workers["dev-2"].queue.qsize() == 1
        assert scheduler._workers["dev-1"].task is not scheduler._workers["dev-2"].task

    asyncio.run(body())


def test_worker_runs_jobs_back_to_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """队列之间不睡：取一项跑一项。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [])
        scheduler.start()
        for door_no in (3, 4):
            scheduler._enqueue_assign(_assign_job(door_no))
        await asyncio.wait_for(scheduler._workers["dev-1"].queue.join(), timeout=3)

        assert [call[2]["door_no"] for call in calls] == [3, 4]

    asyncio.run(body())


def test_worker_survives_a_failing_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次失败只该带走那一项，不该终止这台设备的处理。"""

    async def body() -> None:
        _prepare(monkeypatch, [])
        scheduler.start()

        message = "设备端炸了"

        async def boom(_job: object) -> None:
            raise RuntimeError(message)

        monkeypatch.setattr(scheduler, "_perform_settle", boom)
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT, THRESHOLDS[FEATURE_RENEW_WAIT])
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
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT, THRESHOLDS[FEATURE_RENEW_WAIT])
        worker = scheduler._workers["dev-1"]
        assert worker.timers
        assert worker.task is not None

        scheduler.stop()

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
        "doors": [{"door_no": 3, "cert_no": "99990052", **door}],
    }


def test_snapshots_reads_only_the_derived_layer(fresh_db: str) -> None:
    """取数只走 `usage_session` + `door_live`，原始帧一概不读。"""
    doors_service.process_snapshot("dev-1", _frame(card_no=1001, open_time="2026-10-05T11:00:00.000Z"))

    found = scheduler._snapshots("dev-1")

    assert len(found) == 1
    assert found[0].state is DoorState.WAITING
    assert found[0].door_no == 3
    assert found[0].cert_no == "99990052"
    assert found[0].assigned_at == "2026-10-05T11:00:00.000Z"
    assert found[0].renew_count == 0
    assert found[0].session_id > 0


def test_snapshots_skips_a_session_without_a_live_row(fresh_db: str) -> None:
    """会话还在、门的现态却没了：不判，等下一帧。"""
    doors_service.process_snapshot("dev-1", _frame(card_no=1001, open_time="2026-10-05T11:00:00.000Z"))
    get_conn().execute("DELETE FROM door_live WHERE device_id = ?", ("dev-1",))

    assert scheduler._snapshots("dev-1") == []


def test_renew_count_counts_sessions_in_the_same_run(fresh_db: str) -> None:
    """数的是同一串里的会话条数：第一场是学生自己的分配，不算续。"""
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at="2026-10-05T11:00:00.000Z", card_no=1001, open_time="2026-10-05T11:00:00.000Z")
    )
    assert scheduler._snapshots("dev-1")[0].renew_count == 0

    # 我们自己续出来的那一场：门空了、紧接着又被分配回同一个喷头
    doors_service.process_snapshot("dev-1", _frame(captured_at="2026-10-05T11:05:00.000Z"))
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at="2026-10-05T11:05:04.000Z", card_no=1001, open_time="2026-10-05T11:05:04.000Z")
    )

    assert scheduler._snapshots("dev-1")[0].renew_count == 1


def test_renew_count_starts_over_after_a_real_break(fresh_db: str) -> None:
    """喷头空着超过 300 秒，说明那个人的占用真的结束了——次数从头数。"""
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at="2026-10-05T11:00:00.000Z", card_no=1001, open_time="2026-10-05T11:00:00.000Z")
    )
    doors_service.process_snapshot("dev-1", _frame(captured_at="2026-10-05T11:05:00.000Z"))
    # 中间空了一小时才被重新分配
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at="2026-10-05T12:05:00.000Z", card_no=1001, open_time="2026-10-05T12:05:00.000Z")
    )

    assert scheduler._snapshots("dev-1")[0].renew_count == 0


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

    async def fake_send(_device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)

    # 一小时前分配、一直没开阀：预约续期的 270 秒早已过点
    doors_service.process_snapshot("dev-1", _frame(captured_at=_ago(3600), card_no=1001, open_time=_ago(3600)))

    async def body() -> None:
        session_id = scheduler._snapshots("dev-1")[0].session_id
        scheduler.start()
        scheduler.on_frame("dev-1")  # 网关的挂钩点
        await _let_the_loop_run()

        # 结算发出去了，并且挂上了"等门空闲再补分配"
        assert [action for action, _params in calls] == ["session.settle"]
        assert calls[0][1] == {"cert_no": "99990052", "password": "000000"}
        assert ("dev-1", 3) in scheduler._pending_assigns
        assert ("dev-1", session_id, FEATURE_RENEW_WAIT) in scheduler._attempted

        # 门被释放的那一帧：会话收尾，待分配接上
        doors_service.process_snapshot("dev-1", _frame(captured_at=_ago(3000)))
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle", "session.assign"]
        assert calls[1][1] == {"door_no": 3, "cert_no": "99990052", "password": "000000"}
        assert scheduler._pending_assigns == {}

    with helpers.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_the_same_session_is_settled_only_once(
    fresh_db: str, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """单次执行：帧再来几次也不会为同一个会话再下一单。"""
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    doors_service.process_snapshot("dev-1", _frame(captured_at=_ago(3600), card_no=1001, open_time=_ago(3600)))

    async def body() -> None:
        scheduler.start()
        for _ in range(3):
            scheduler.on_frame("dev-1")
            await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]

    with helpers.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_auto_settle_only_settles_and_never_reassigns(
    fresh_db: str, anchored: None, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动结算只做前半步：结完就完了，不补分配。

    会话才走了 5 分钟（会话续期还早），而关阀已经 2 分钟（超过 60 秒的阈值）——所以
    动手的是自动结算。暂停时刻取自 `door_live.close_time`，所以这台设备要有偏差记录。
    """
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at=_ago(120), card_no=1001, open_time=_ago(300), close_time=_ago(120), bath_time_s=120)
    )

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]
        assert scheduler._pending_assigns == {}

    with helpers.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())


def test_a_student_outside_the_roster_is_never_touched(
    fresh_db: str, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """名单是"允许对谁动手"的唯一出处：不在名单里，一帧下来什么都不发生。

    设备在这里也要**已信任且在线**——否则"什么都没发生"可能只是因为设备没被信任，
    那这条测试就测了个空。
    """
    calls: list[tuple[str, JsonObject]] = []

    async def fake_send(_device_id: str, action: str, params: JsonObject, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    doors_service.process_snapshot(
        "dev-1", _frame(captured_at=_ago(3600), card_no=1001, cert_no="2530999999", open_time=_ago(3600))
    )

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")

        assert calls == []
        assert scheduler._workers["dev-1"].timers == {}

    with helpers.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())
