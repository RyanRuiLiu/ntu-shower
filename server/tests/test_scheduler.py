"""会话维护的调度与执行：定时器编排、复查、单次执行、串行队列、待补分配。

取数与命令下发都用 monkeypatch 换掉——这一层要验的是**编排**，不是 SQL 也不是协议。
每条都跑在真实的 asyncio 循环里，因为定时器与队列都是循环上的东西。
"""

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest

from app.core import outcomes
from app.core.db import get_conn
from app.core.protocol import JsonObject
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState
from app.modules.sessions import scheduler
from app.modules.sessions.decision import FEATURE_AUTO_SETTLE, FEATURE_RENEW_WAIT, Candidate

CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": "2530110052",
    "renew_wait_threshold_s": 270,
    "renew_wait_max_count": 3,
    "renew_session_enabled": True,
    "renew_session_students": "2530110052",
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": "2530110052",
    "auto_settle_pause_threshold_s": 60,
    "student_passwords": {"2530110052": "000000"},
}

WAITING = Candidate(
    session_id=7,
    device_id="dev-1",
    door_no=3,
    cert_no="2530110052",
    state=DoorState.WAITING,
    assigned_at="2026-10-05T11:00:00.000Z",  # 早已过点：必然排上
    close_time="",
    renew_count=0,
)


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
    """把取数与下发换成可控的桩，返回记录下来的命令。"""
    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(scheduler, "_snapshots", lambda _device_id: candidates)
    monkeypatch.setattr(
        scheduler.doors_repository,
        "get_all_live",
        lambda _device_id: live if live is not None else {3: {"state": "free", "close_time": ""}},
    )
    monkeypatch.setattr(scheduler.doors_repository, "get_active_by_cert", lambda _device_id, _cert: active_by_cert)
    calls = sent if sent is not None else []

    async def fake_send(device_id: str, action: str, params: dict[str, object], **_kw: object) -> object:
        calls.append((device_id, action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.sessions_service, "send_command_and_wait", fake_send)
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
            cert_no="2530110052",
            state=DoorState.PAUSED,
            assigned_at="2026-10-05T11:00:00.000Z",
            close_time="2026-10-05T11:00:00.000Z",
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
        scheduler._attempted.add((WAITING.session_id, FEATURE_RENEW_WAIT))
        scheduler.reschedule("dev-1")

        assert scheduler._workers["dev-1"].timers == {}

    asyncio.run(body())


def test_closed_sessions_are_forgotten(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话结束时清掉它的记录，否则集合一直长。"""
    monkeypatch.setattr(scheduler.doors_repository, "list_open_sessions_of", lambda _device_id: [])
    scheduler._attempted.add((7, FEATURE_RENEW_WAIT))

    scheduler._forget_closed_sessions("dev-1")

    assert scheduler._attempted == set()


# ── 到点 ──


def test_fire_skips_when_recheck_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """到点只说明该看一眼了；现态变了就放弃，而且**不告警**。"""

    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [])  # 会话没了
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(FEATURE_RENEW_WAIT, 0.0))

        assert scheduler._workers["dev-1"].queue.empty()

    asyncio.run(body())


def test_fire_enqueues_a_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._fire("dev-1", WAITING, scheduler.Trigger(FEATURE_RENEW_WAIT, 0.0))

        assert scheduler._workers["dev-1"].queue.qsize() == 1
        assert 3 in scheduler._workers["dev-1"].busy

    asyncio.run(body())


def test_same_door_is_not_queued_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        scheduler.start()
        _prepare(monkeypatch, [WAITING])
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT)
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT)

        assert scheduler._workers["dev-1"].queue.qsize() == 1

    asyncio.run(body())


# ── 执行 ──


def test_settle_stops_when_there_is_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """名单里有他却没有他的凭证：发不出去，也不能假装发了。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: {**CONFIG, "student_passwords": {}})
        await scheduler._perform_settle(scheduler._SettleJob(candidate=WAITING, feature=FEATURE_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_renewal_arms_a_pending_assign_but_settle_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """续期类要在结算之后再分配；自动结算只要前半步。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(scheduler._SettleJob(candidate=WAITING, feature=FEATURE_RENEW_WAIT))
        assert ("dev-1", 3) in scheduler._pending_assigns

        scheduler._pending_assigns.clear()
        await scheduler._perform_settle(scheduler._SettleJob(candidate=WAITING, feature=FEATURE_AUTO_SETTLE))
        assert scheduler._pending_assigns == {}

    asyncio.run(body())


def test_settle_records_the_attempt_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """先记后发：否则失败之后重算出的到点还是同一刻，会自旋。"""

    async def body() -> None:
        _prepare(monkeypatch, [WAITING])
        await scheduler._perform_settle(scheduler._SettleJob(candidate=WAITING, feature=FEATURE_RENEW_WAIT))

        assert (WAITING.session_id, FEATURE_RENEW_WAIT) in scheduler._attempted

    asyncio.run(body())


def test_settle_stops_when_the_roster_changed_underneath(monkeypatch: pytest.MonkeyPatch) -> None:
    """复查到下单之间隔着取数与排队，所以下单前要再问一次名单。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [WAITING], sent=[])
        monkeypatch.setattr(
            scheduler.config_store, "effective", lambda _device_id: {**CONFIG, "renew_wait_enabled": False}
        )
        await scheduler._perform_settle(scheduler._SettleJob(candidate=WAITING, feature=FEATURE_RENEW_WAIT))

        assert calls == []

    asyncio.run(body())


def test_already_gone_is_not_a_failure() -> None:
    """服务端找不到占用中的喷头，正是"他已经走了/已经结过了"——正常收尾，不告警。"""
    ok: JsonObject = {"status": outcomes.OK.value}
    gone: JsonObject = {"status": outcomes.FAILED.value, "reason_code": scheduler.NO_OCCUPIED_DOOR}
    other: JsonObject = {"status": outcomes.FAILED.value, "reason_code": "something_else"}

    assert scheduler._settled_ok(ok, WAITING, FEATURE_AUTO_SETTLE) is True
    assert scheduler._settled_ok(gone, WAITING, FEATURE_AUTO_SETTLE) is False
    assert scheduler._settled_ok(other, WAITING, FEATURE_AUTO_SETTLE) is False


def test_assign_is_sent_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次执行：失败也不重发。"""

    async def body() -> None:
        calls = _prepare(monkeypatch, [])
        await scheduler._perform_assign(
            scheduler._AssignJob(device_id="dev-1", door_no=3, cert_no="2530110052", password="000000")
        )

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
        "cert_no": "2530110052",
        "password": "000000",
        "expires_at": 1e9,
    }
    fields.update(overrides)
    scheduler._pending_assigns[("dev-1", 3)] = scheduler._PendingAssign(**fields)  # type: ignore[arg-type]


def test_pending_assign_waits_while_the_door_is_still_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    """分配的前置是服务端先看见门空了；门还占着就等下一帧。"""

    async def body() -> None:
        _arm(monkeypatch, live={3: {"state": "bathing", "close_time": ""}})
        scheduler._flush_pending_assign("dev-1")

        # 还没轮到它：待办留着，队列也没建起来
        assert ("dev-1", 3) in scheduler._pending_assigns
        assert scheduler._workers == {}

    asyncio.run(body())


def test_pending_assign_waits_while_the_session_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话还没被服务端收尾时分配会被拒：也等下一帧。"""

    async def body() -> None:
        _arm(monkeypatch, active_by_cert={"id": 7})
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) in scheduler._pending_assigns

    asyncio.run(body())


def test_pending_assign_fires_once_the_door_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    async def body() -> None:
        _arm(monkeypatch)
        scheduler._flush_pending_assign("dev-1")

        assert ("dev-1", 3) not in scheduler._pending_assigns
        assert scheduler._workers["dev-1"].queue.qsize() == 1

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
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT)
        scheduler._enqueue_settle(other, FEATURE_AUTO_SETTLE)

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
            scheduler._enqueue_assign(
                scheduler._AssignJob(device_id="dev-1", door_no=door_no, cert_no="2530110052", password="000000")
            )
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
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT)
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
        scheduler._enqueue_settle(WAITING, FEATURE_RENEW_WAIT)
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
        "doors": [{"door_no": 3, "cert_no": "2530110052", **door}],
    }


def test_snapshots_reads_only_the_derived_layer(fresh_db: str) -> None:
    """取数只走 `usage_session` + `door_live`，原始帧一概不读。"""
    doors_service.process_snapshot("dev-1", _frame(card_no=1001, open_time="2026-10-05T11:00:00.000Z"))

    found = scheduler._snapshots("dev-1")

    assert len(found) == 1
    assert found[0].state is DoorState.WAITING
    assert found[0].door_no == 3
    assert found[0].cert_no == "2530110052"
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
