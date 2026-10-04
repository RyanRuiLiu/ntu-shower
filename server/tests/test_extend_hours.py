"""延长服务时间：到点拨钟、等门清空后重新分配、保持时长到了调回。

夹具要三样：一台**有偏差记录**的设备（否则算不出"设备表盘上现在几点"，而整个判定都
落在那个轴上）、一份名单、以及可替换的桩——命令下发与分配都换成桩，这一层测的是判定
与状态机，不是界面流程本身（那是 test_background_tasks_real_data 的活）。
"""

import asyncio

import pytest
from tests import helpers

from app.core import actions, device_clock
from app.core.clock import shift_rfc3339
from app.core.problems import DOOR_NOT_FREE, ApiProblemError
from app.modules.config import store as config_store
from app.modules.doors import service as doors_service
from app.modules.extend_hours import repository
from app.modules.extend_hours import service as extend_hours
from app.modules.sessions.schemas import AssignRequest
from test_scheduler import _frame

DEVICE = "dev-extend"

#: 名单里有两个人，其中只有一个占着喷头——另一份名单项用来验"没占用的人不被分配"。
CONFIG: dict[str, object] = {
    "extend_hours_enabled": True,
    "extend_hours_at": "23:49",
    "extend_hours_hold_s": 3600,
    "extend_hours_zone_offset_minutes": 480,
    "extend_hours_students": {"2530110052": "000000", "2217110057": "111111"},
}

#: 设备钟与服务端对齐（偏差 0）。用例里的时刻一律读作**设备表盘上的**时刻，服务端
#: 时刻由它减去时区偏移反推——这样断言里出现的都是墙上那个字面时间。
SKEW_S = 0.0
ZONE_MINUTES = 480


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    local_now: str,
    sent: list[tuple[str, object]] | None = None,
) -> str:
    """把"现在"、配置与命令下发换成可控的桩；返回与之对应的**服务端时刻**。

    `local_now` 是设备表盘上的时刻，而判定读的是 `now_rfc3339()`（服务端轴），所以这里
    把服务端时刻反推出来并桩进去：表盘 = 服务端 − 偏差 + 时区，偏差取 0 时
    服务端 = 表盘 − 时区。返回它是为了让"一小时前拨的"这种用例能在正确的轴上算时刻。
    """
    device_clock.load(DEVICE, SKEW_S)
    # 必须登记：tick_once 遍历的是 `device` 表里的设备，没登记的话它连看都不看这台
    helpers.register_trusted_device(DEVICE)
    server_now = shift_rfc3339(local_now, -ZONE_MINUTES * 60)
    assert server_now is not None
    monkeypatch.setattr(extend_hours, "now_rfc3339", lambda: server_now)
    monkeypatch.setattr(config_store, "effective", lambda _device_id: CONFIG)

    async def fake_send(_device_id: str, action: str, params: object, **_kw: object) -> object:
        if sent is not None:
            sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "send_command_and_wait", fake_send)
    return server_now


def _with_session(open_time: str, cert_no: str = "2530110052", door_no: int = 3) -> None:
    """造一场未结束的会话：分配了、还没开阀。"""
    doors_service.process_snapshot(
        DEVICE,
        _frame(
            captured_at=open_time, card_no=0, cert_no=cert_no, acc_no="1010209501", open_time=open_time, door_no=door_no
        ),
    )


# ── 表 ────────────────────────────────────────────────────────────────────


def test_one_row_per_device_and_clear_removes_it(fresh_db: str) -> None:
    repository.record(DEVICE, "2026-10-05T15:49:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    assert repository.get(DEVICE) is not None
    assert [row["device_id"] for row in repository.list_shifted()] == [DEVICE]

    # 覆盖式写入：一台设备同时只可能偏着一次
    repository.record(DEVICE, "2026-10-05T15:50:00.000Z", [])
    assert str(repository.get(DEVICE)["shifted_at"]) == "2026-10-05T15:50:00.000Z"

    repository.clear(DEVICE)
    assert repository.get(DEVICE) is None
    assert repository.list_shifted() == []


def test_parse_assignments_tolerates_hand_edited_rows(fresh_db: str) -> None:
    """坏记录只可能来自人手工改库：按"没有可分配的人"处理，不抛。"""
    repository.record(DEVICE, "2026-10-05T15:49:00.000Z", [])
    from app.core.db import write

    write("UPDATE clock_shift SET assignments = ? WHERE device_id = ?", ("{not json", DEVICE))

    assert repository.parse_assignments(repository.get(DEVICE)) == []  # type: ignore[arg-type]


# ── 时刻换算 ──────────────────────────────────────────────────────────────


def test_reached_compares_on_the_device_clock_axis() -> None:
    """两个时刻都在"设备表盘"这条轴上，直接比大小。"""
    assert extend_hours._reached("2026-10-05T23:49:00.000Z", "23:49")
    assert extend_hours._reached("2026-10-05T23:50:00.000Z", "23:49")
    assert not extend_hours._reached("2026-10-05T23:48:00.000Z", "23:49")
    # 判不了就不动手：时刻取不出来，或配置值不合法
    assert not extend_hours._reached(None, "23:49")
    assert not extend_hours._reached("2026-10-05T23:50:00.000Z", "不是时刻")


def test_local_now_needs_a_skew_record(fresh_db: str) -> None:
    """没有偏差记录时给 None，而不是拿 0 当偏差——那等于假定设备钟与我们对齐。"""
    assert extend_hours._local_now("dev-no-skew", 480) is None

    device_clock.load("dev-no-skew", 0.0)
    assert extend_hours._local_now("dev-no-skew", 480) is not None


def test_local_now_adds_the_zone_offset(fresh_db: str) -> None:
    device_clock.load(DEVICE, 0.0)

    utc = extend_hours._local_now(DEVICE, 0)
    beijing = extend_hours._local_now(DEVICE, 480)

    assert utc is not None
    assert beijing is not None
    # 同一个设备钟读数，换个时区偏移就落在表盘的另一处
    assert beijing > utc


# ── 拨钟 ──────────────────────────────────────────────────────────────────


def test_shift_writes_the_clock_then_restarts_the_bath_app(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """两件事成对且有序：先写钟再重启，否则 App 读到的是旧时刻。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z")

    asyncio.run(extend_hours.tick_once())

    assert [action for action, _params in sent] == [actions.CLOCK_SET, actions.BATH_RESTART]
    # 写进去的是"拨完之后的设备表盘时刻"：23:50 加满一小时
    assert sent[0][1] == {"datetime": "2026-10-06 00:50:00"}


def test_shift_is_skipped_when_disabled(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setitem(CONFIG, "extend_hours_enabled", False)
    _with_session("2026-10-05T23:40:00.000Z")

    asyncio.run(extend_hours.tick_once())

    assert sent == []


def test_shift_is_skipped_when_nobody_is_using_it(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """名单里没人占着就不拨：白拨一次会让设备钟无谓地偏一小时。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z", cert_no="9999999999")

    asyncio.run(extend_hours.tick_once())

    assert sent == []
    assert repository.get(DEVICE) is None


def test_failed_shift_leaves_no_row_so_the_next_tick_retries(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨失败要清掉那一行：它是"正在延长中"的锁，留着就永远不再试了。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z")

    async def failing(_device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "send_command_and_wait", failing)

    asyncio.run(extend_hours.tick_once())

    assert repository.get(DEVICE) is None


def test_the_row_keeps_who_was_where(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨之前那一刻的"谁在哪个喷头"是唯一能记下它的时机。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z", door_no=5)

    asyncio.run(extend_hours.tick_once())

    row = repository.get(DEVICE)
    assert row is not None
    assert repository.parse_assignments(row) == [{"door_no": 5, "cert_no": "2530110052"}]


# ── 调回 ──────────────────────────────────────────────────────────────────


def test_restore_waits_for_the_hold_duration(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    server_now = _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    repository.record(DEVICE, server_now, [])
    repository.mark_restarted(DEVICE, server_now)

    asyncio.run(extend_hours.tick_once())

    assert sent == []
    assert repository.get(DEVICE) is not None


def test_restore_moves_the_clock_back_and_clears_the_row(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    server_now = _prepare(monkeypatch, "2026-10-06T00:50:00.000Z", sent)
    # 一小时前拨的：已经满了保持时长。重启早就做过了，所以现在轮到调回
    repository.record(DEVICE, shift_rfc3339(server_now, -3600) or "", [])
    repository.mark_restarted(DEVICE, shift_rfc3339(server_now, -3590) or "")

    asyncio.run(extend_hours.tick_once())

    assert [action for action, _params in sent] == [actions.CLOCK_SET, actions.BATH_RESTART]
    assert repository.get(DEVICE) is None


def test_failed_restore_keeps_the_row(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """调回失败不能放弃：钟偏着会让之后所有数据的时刻都错一小时。"""
    _prepare(monkeypatch, "2026-10-06T00:50:00.000Z")
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [])

    async def failing(_device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "timeout", "reason_code": "server.deadline-exceeded", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "send_command_and_wait", failing)

    asyncio.run(extend_hours.tick_once())

    assert repository.get(DEVICE) is not None


# ── 重新分配 ──────────────────────────────────────────────────────────────


def test_on_frame_does_nothing_without_a_row(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_device_id: str, req: AssignRequest) -> object:
        assigned.append(req)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", fake_assign)

    extend_hours.on_frame(DEVICE)

    assert assigned == []


def test_on_frame_assigns_back_who_is_missing(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨钟已经把喷头清空了，帧一到就把名单里的人分配回原喷头。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_device_id: str, req: AssignRequest) -> object:
        assigned.append((req.door_no, req.cert_no, req.password))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", fake_assign)
    repository.record(
        DEVICE,
        "2026-10-05T23:50:00.000Z",
        [{"door_no": 3, "cert_no": "2530110052"}, {"door_no": 5, "cert_no": "9999999999"}],
    )

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        # on_frame 同步返回、分配在后台任务里跑；等它做完
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    # 名单里没有的那个学号被跳过，名单里有的那个拿回原来的喷头
    assert assigned == [(3, "2530110052", "000000")]


def test_reassign_skips_someone_already_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """他已经在占用态就跳过：这一步靠"查现状"决定做不做，不必另记状态。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:55:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_device_id: str, req: AssignRequest) -> object:
        assigned.append(req)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", fake_assign)
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert assigned == []


# ── 生命周期 ──────────────────────────────────────────────────────────────


def test_start_and_stop_are_idempotent(fresh_db: str) -> None:
    """与其余后台任务同一种形态：重复 start 不叠加，stop 之后可以再 start。"""

    async def body() -> None:
        extend_hours.start()
        first = extend_hours._task
        extend_hours.start()
        assert extend_hours._task is first

        extend_hours.stop()
        assert extend_hours._task is None

    asyncio.run(body())


def test_every_shift_is_logged_for_the_operator(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """留痕靠两样：动作本身进 command_audit（由命令下发那条路覆盖），决定进日志。

    这一条盯的是后者——`background_task_run` 的 door_no/cert_no 都非空，装不下设备级的
    事件，所以"为什么动手"只能从日志里读。
    """
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))

    asyncio.run(extend_hours.tick_once())

    assert "extend-hours.shifted" in events


def test_helpers_are_available(fresh_db: str) -> None:
    """`helpers` 的导入留住：其余用例从它取已信任设备的登记方式。"""
    helpers.register_trusted_device(DEVICE)
    from app.modules.devices import service as devices_service

    record = devices_service.get_device(DEVICE)
    assert record is not None
    assert record["trusted"]


# ── 半成功与早退 ──────────────────────────────────────────────────────────


def test_restart_failure_is_reported_separately(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """钟拨走了而 App 没重启：计划按旧钟算，等于白拨。

    它要单独记一句，而不是并进"拨钟失败"——那会让人以为钟也没动。重启失败**不清行**，
    下一轮还会再试。
    """
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    server_now = _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    # 拨钟在一分钟前：分配的窗口已经过了，于是不必等人齐就该重启
    repository.record(DEVICE, shift_rfc3339(server_now, -120) or "", [{"door_no": 3, "cert_no": "2530110052"}])

    async def half(_device_id: str, action: str, _params: object, **_kw: object) -> object:
        if action == actions.BATH_RESTART:
            return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "send_command_and_wait", half)

    asyncio.run(extend_hours.tick_once())

    assert "extend-hours.restart-failed" in events
    row = repository.get(DEVICE)
    assert row is not None
    assert str(row["restarted_at"] or "") == ""


def test_shift_is_skipped_without_a_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """算不出设备钟就不动它：拿 0 当偏差等于假定那口钟与我们对齐。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z")
    device_clock.clear()

    asyncio.run(extend_hours.tick_once())

    assert sent == []
    assert repository.get(DEVICE) is None


def test_on_frame_ignores_a_row_with_nothing_to_assign(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [])

    extend_hours.on_frame(DEVICE)

    assert extend_hours._reassign_tasks == set()


def test_on_frame_needs_a_roster(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setitem(CONFIG, "extend_hours_students", {})
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    extend_hours.on_frame(DEVICE)

    assert extend_hours._reassign_tasks == set()


def test_reassign_reports_a_failed_command(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令本身失败时，领域层**不抛**、只把结果返回来。

    不看那一句的结果就是记下一条"已分配回去"的假日志，而那个门其实空着。重启之后再没有
    帧可等，也没人会来纠正它——所以这一条断言的是"没有那句假成功"。
    """
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    async def failed(_device_id: str, _req: AssignRequest) -> dict[str, object]:
        return {"status": "timeout", "reason_code": "server.deadline-exceeded", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", failed)

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert "extend-hours.reassign-failed" in events
    assert "extend-hours.reassigned" not in events


def test_a_refused_command_is_not_marked_as_a_failure(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """领域层拒了 = 命令根本没下发，所以**不算失败**，下一轮还要试。

    这与"命令发了而没成"是两回事：前者只是"现在还不该做"，后者才终结。
    """
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    async def refusing(_device_id: str, _req: AssignRequest) -> object:
        raise ApiProblemError(DOOR_NOT_FREE, "喷头 3 当前状态：bathing")

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", refusing)

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert "extend-hours.reassign-waiting" in events
    assert extend_hours._reassign_failed == set()


def test_a_failed_command_is_not_tried_again(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令发了而没成 = 失败，同一个人不再试——每轮再发一遍不会有不同的结果。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])
    attempts: list[int] = []

    async def failed(_device_id: str, _req: AssignRequest) -> object:
        attempts.append(1)
        return {"status": "timeout", "reason_code": "server.deadline-exceeded", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", failed)

    async def body() -> None:
        for _ in range(2):
            extend_hours.on_frame(DEVICE)
            await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert len(attempts) == 1


def test_the_failure_memory_is_cleared_when_the_extension_ends(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """调回之后记忆要清：不然下一次延长里那个人会被永久跳过。"""
    server_now = _prepare(monkeypatch, "2026-10-06T00:50:00.000Z")
    repository.record(DEVICE, shift_rfc3339(server_now, -3600) or "", [])
    repository.mark_restarted(DEVICE, shift_rfc3339(server_now, -3590) or "")
    extend_hours._reassign_failed.add((DEVICE, "2530110052"))

    asyncio.run(extend_hours.tick_once())

    assert repository.get(DEVICE) is None
    assert extend_hours._reassign_failed == set()


def test_restart_waits_until_everyone_is_back(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """人还没齐就不重启——重启会 force-stop，把正在跑的分配打断。

    这里名单里那位还没进占用态，而窗口没到，所以这一轮只做分配、不重启。
    """
    sent: list[tuple[str, object]] = []
    server_now = _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    repository.record(DEVICE, shift_rfc3339(server_now, -10) or "", [{"door_no": 3, "cert_no": "2530110052"}])

    async def fake_assign(_device_id: str, _req: AssignRequest) -> object:
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", fake_assign)

    asyncio.run(extend_hours.tick_once())

    # 试过分配，但没有重启——他还没真的进占用态，而窗口还有 50 秒
    assert actions.BATH_RESTART not in [action for action, _params in sent]
    row = repository.get(DEVICE)
    assert row is not None
    assert str(row["restarted_at"] or "") == ""


def test_an_emptied_roster_still_restarts(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """名单被清空时没人可分配，但那不等于"不用重启"——重启关系到 MCU。"""
    sent: list[tuple[str, object]] = []
    server_now = _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setitem(CONFIG, "extend_hours_students", {})
    repository.record(DEVICE, shift_rfc3339(server_now, -10) or "", [{"door_no": 3, "cert_no": "2530110052"}])

    asyncio.run(extend_hours.tick_once())

    assert [action for action, _params in sent] == [actions.BATH_RESTART]
    row = repository.get(DEVICE)
    assert row is not None
    assert str(row["restarted_at"] or "") != ""


def test_reassign_reports_when_the_domain_layer_refuses(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """领域层拒了只是"现在还不该做"：记一句、下一帧再来，不丢待办。"""
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    repository.record(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2530110052"}])

    async def refusing(_device_id: str, _req: object) -> object:
        raise ApiProblemError(DOOR_NOT_FREE, "喷头 3 当前状态：bathing")

    monkeypatch.setattr(extend_hours.sessions_routes, "perform_assign", refusing)

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert "extend-hours.reassign-waiting" in events
    # 那一行仍在：延长还没结束
    assert repository.get(DEVICE) is not None
