"""延长服务时间：到点拨钟、滚动续写截止、过午夜且无人占用后拨回。

夹具要三样：一台**有偏差记录**的设备（否则算不出"设备表盘上现在几点"，而整个判定都
落在那个轴上）、一份名单、以及可替换的桩——命令下发与分配都换成桩，这一层测的是判定
与状态机，不是界面流程本身（那是 test_job_runs 的活）。

滚动重布的时间参数在用例里以"表盘时刻"断言。延长期间设备钟快 15 分钟（拨快幅度），
SKEW_FAST_S 取 -900 模拟"偏差已被测量"的状态：`_local_now` 给出的是表盘读数，恢复条件
的"过午夜"用真实轴（`to_local(now / shifted_at)`）的日期差判定，两者互不干扰。
"""

import asyncio

import pytest
from tests import device_link, door_link

from app.core import actions, device_clock
from app.core.clock import parse_rfc3339, shift_rfc3339
from app.core.problems import DOOR_NOT_FREE, ApiProblemError
from app.db.session import session_scope
from app.modules.config import store as config_store
from app.modules.extend_hours import crud as repository
from app.modules.extend_hours import service as extend_hours
from app.modules.sessions.decision import students_of
from app.modules.sessions.schemas import AssignRequest
from test_scheduler import _frame

DEVICE = "dev-extend"

#: 名单里有两个人，其中只有一个占着喷头——另一份名单项用来验"没占用的人不被分配"。
CONFIG: dict[str, object] = {
    "extend_hours_enabled": True,
    "extend_hours_at": "23:49",
    "extend_shift_minutes": 15,
    "extend_hours_zone_offset_minutes": 480,
    "extend_hours_students": {"2026000001": "000000", "2026000003": "111111"},
    "power_off_at": "23:58",
    "power_on_at": "10:55",
    "time_sync_enabled": True,
    "time_sync_threshold_s": 60,
}

#: 设备钟快 15 分钟（延长拨快后的状态）：偏差 = 服务端 − 设备 = -900 秒。
SKEW_FAST_S = -900.0

#: 设备时区偏移（分钟）：UTC+8。所有用例共用。
ZONE_MINUTES = 480


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    local_now: str,
    sent: list[tuple[str, object]] | None = None,
    skew_s: float = 0.0,
) -> str:
    """把"现在"、配置与命令下发换成可控的桩；返回与之对应的**服务端时刻**。

    `local_now` 是设备表盘上的时刻，而判定读的是 `now_rfc3339()`（服务端轴），所以这里
    把服务端时刻反推出来并桩进去：表盘 = 服务端 − 偏差 + 时区。返回它是为了让"十分钟前
    拨的"这种用例能在正确的轴上算时刻。
    """
    device_clock.load(DEVICE, skew_s)
    # 必须登记：tick_once 遍历的是 `device` 表里的设备，没登记的话它连看都不看这台
    device_link.register_trusted_device(DEVICE)
    # 放回/失败/滚动限频的记忆是模块级的：上一个用例的痕迹会让这一个用例失真
    extend_hours._reassigned.clear()
    extend_hours._reassign_failed.clear()
    extend_hours._last_roll_at.clear()
    extend_hours._last_roll.clear()
    server_now = shift_rfc3339(local_now, -ZONE_MINUTES * 60)
    assert server_now is not None
    monkeypatch.setattr(extend_hours, "now_rfc3339", lambda: server_now)
    monkeypatch.setattr(config_store, "effective", lambda _session, _device_id: CONFIG)

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        if sent is not None:
            sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", fake_send)
    return server_now


def _with_session(open_time: str, cert_no: str = "2026000001", door_no: int = 3) -> None:
    """造一场未结束的会话：分配了、还没开阀。"""
    door_link.feed(
        DEVICE,
        _frame(
            captured_at=open_time, card_no=0, cert_no=cert_no, acc_no="7000000001", open_time=open_time, door_no=door_no
        ),
    )


def _clear_door(captured_at: str, door_no: int = 3) -> None:
    """浴室端清场：门变空的那一帧（挂着的会话随之关闭）。"""
    door_link.feed(DEVICE, _frame(captured_at=captured_at, card_no=0, cert_no="", door_no=door_no))


def _record_shift(device_id: str, shifted_at: str, assignments: list[dict[str, object]]) -> None:
    """测试种子：登记设备并把 RFC 3339 文本时刻写成一行拨钟状态。

    行跟着设备走（外键），所以登记是种子的前提；时刻转 datetime 落库，
    读回来仍是同一字符串（format_rfc3339 往返）。
    """
    device_link.register_trusted_device(device_id)
    moment = parse_rfc3339(shifted_at)
    assert moment is not None
    with session_scope() as session:
        repository.record(session, device_id, moment, assignments)


def _clear_shift(device_id: str) -> None:
    with session_scope() as session:
        repository.clear(session, device_id)


def _get_shift(device_id: str) -> dict[str, object] | None:
    with session_scope() as session:
        return repository.get(session, device_id)


def _list_shifted() -> list[dict[str, object]]:
    with session_scope() as session:
        return repository.list_shifted(session)


def _shift(shifted_at: str, assignments: tuple[tuple[int, str], ...] = ((3, "2026000001"),)) -> "extend_hours.Shift":
    """一次拨钟的载体：`_reassign` 靠 `shifted_at` 分辨"拨钟前的残留会话"与"已经放回的"。"""
    return extend_hours.Shift(device_id=DEVICE, shifted_at=shifted_at, assignments=assignments)


# ── 表 ────────────────────────────────────────────────────────────────────


def test_one_row_per_device_and_clear_removes_it(fresh_db: str) -> None:
    _record_shift(DEVICE, "2026-10-05T15:49:00.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    assert _get_shift(DEVICE) is not None
    assert [row["device_id"] for row in _list_shifted()] == [DEVICE]

    # 覆盖式写入：一台设备同时只可能偏着一次
    _record_shift(DEVICE, "2026-10-05T15:50:00.000Z", [])
    assert str(_get_shift(DEVICE)["shifted_at"]) == "2026-10-05T15:50:00.000Z"

    _clear_shift(DEVICE)
    assert _get_shift(DEVICE) is None
    assert _list_shifted() == []


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


# ── 拨钟：预补偿重布 + 拨快 ──────────────────────────────────────────────


def test_shift_rearms_power_with_precompensated_target_then_writes_the_clock(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两件事成对且有序：先重布（目标预补偿拨走的 15 分钟）再写钟——拨快会让旧截止
    立刻越界即当场断电（实测）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z")

    _tick()

    # 预补偿续写武装了"拨快后表盘再 +10 分钟"的截止，同轮推进的续写被限频跳过：
    # 武装未满 2 分钟，不必重复下发
    assert [action for action, _params in sent] == [actions.POWER_SCHEDULE, actions.CLOCK_SET]
    # 预补偿：表盘 23:50 + 拨走的十五分钟 + 滚动窗口十分钟 → 次日 00:15
    assert sent[0][1] == {"off_at": "00:15", "on_at": "10:55"}
    # 写进去的是"拨完之后的设备表盘时刻"：23:50 加满十五分钟
    assert sent[1][1] == {"datetime": "2026-10-06 00:05:00", "rearm": False}
    # 偏差记录当场跟上：设备被拨快 900 秒，偏差（服务端 − 设备）减同量——否则下一次
    # 续写会带着过时偏差，把截止写回拨快前的表盘（实测写回过 00:00）
    assert device_clock.get(DEVICE) == -900.0


def test_shift_is_skipped_when_disabled(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setitem(CONFIG, "extend_hours_enabled", False)
    _with_session("2026-10-05T23:40:00.000Z")

    _tick()

    assert sent == []


def test_shift_is_skipped_when_nobody_is_using_it(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """名单里没人占着就不拨：白拨一次会让设备钟无谓地偏一段时间。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z", cert_no="9999999999")

    _tick()

    assert sent == []
    assert _get_shift(DEVICE) is None


def test_failed_shift_leaves_no_row_so_the_next_tick_retries(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """任一步失败要清掉那一行：它是"正在延长中"的锁，留着就永远不再试了。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z")

    async def failing(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", failing)

    _tick()

    assert _get_shift(DEVICE) is None


def test_the_row_keeps_who_was_where(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨之前那一刻的"谁在哪个喷头"是唯一能记下它的时机。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z", door_no=5)

    _tick()

    row = _get_shift(DEVICE)
    assert row is not None
    assert row["assignments"] == [{"door_no": 5, "cert_no": "2026000001"}]


def test_shift_landing_guard_refuses_to_land_inside_the_hostile_window(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落点守卫：拨快后的钟面必须跨过午夜。幅度配小了（5 分钟落点在 23:55，仍在
    23:50-23:58 的敌对窗口里）就本轮放弃，并记一句操作员能看懂的警告。"""
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    monkeypatch.setitem(CONFIG, "extend_shift_minutes", 5)
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    _with_session("2026-10-05T23:40:00.000Z")

    _tick()

    assert "extend-hours.landing-unsafe" in events
    assert sent == []
    assert _get_shift(DEVICE) is None


# ── 滚动续写与恢复条件 ────────────────────────────────────────────────────


def test_extension_rolls_the_deadline_every_tick(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """延长期间每轮 tick 都把截止续写一个窗口；钟已快 15 分钟，目标按表盘读数 + 窗口。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:50:00.000Z", sent, skew_s=SKEW_FAST_S)
    _record_shift(
        DEVICE,
        shift_rfc3339("2026-10-06T00:50:00.000Z", -ZONE_MINUTES * 60) or "",
        [{"door_no": 3, "cert_no": "2026000001"}],
    )

    _tick()

    # 滚动目标 = 表盘此刻（00:50，快 15 分钟）+ 十分钟 → 01:00
    assert sent == [(actions.POWER_SCHEDULE, {"off_at": "01:15", "on_at": "10:55"})]
    assert _get_shift(DEVICE) is not None


def test_restore_waits_until_past_midnight(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实时间没过午夜：不拨回（敌对窗口的尾巴还在），滚动续写照常。

    设备表盘读 00:10（快 15 分钟）= 真实 23:55，午夜未过；`to_local` 的日期差判它。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:55:00.000Z", sent, skew_s=SKEW_FAST_S)
    # 拨钟在 20 分钟前（真实 10 月 5 日 23:35）：午夜未过
    _record_shift(
        DEVICE,
        shift_rfc3339("2026-10-05T23:55:00.000Z", -ZONE_MINUTES * 60 - 1200) or "",
        [{"door_no": 3, "cert_no": "2026000001"}],
    )

    _tick()

    assert sent == [(actions.POWER_SCHEDULE, {"off_at": "00:20", "on_at": "10:55"})]
    assert _get_shift(DEVICE) is not None


def test_restore_happens_once_midnight_passed_and_nobody_is_occupying(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实时间过了午夜、设备上无人占用：拨回 + 新框架重布，节奏恢复。

    拨回把钟摆回真实时刻（钟面失真清零），重布在新框架里落位：当晚（10 月 6 日）23:58
    断电、次日 10:55 上电。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    _tick()

    assert [action for action, _params in sent] == [actions.POWER_SCHEDULE, actions.CLOCK_SET, actions.POWER_SCHEDULE]
    # 滚动续写在前（表盘 00:45 + 偏差 15 分钟 + 窗口 10 分钟 = 01:10），
    # 随后拨回（钟面失真清零，落回真实 00:45）+ 常规重布
    assert sent[0][1] == {"off_at": "01:10", "on_at": "10:55"}
    assert sent[1][1] == {"datetime": "2026-10-06 00:45:00", "rearm": False}
    assert sent[2][1] == {"off_at": "23:58", "on_at": "10:55"}
    assert _get_shift(DEVICE) is None


def test_restore_holds_while_someone_is_still_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """午夜已过但有人还占着（预约未开阀的、淋浴中的）：不拨回——拨回的钟跳会清掉这些
    会话。滚动续写照常，人不受扰。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])
    _with_session("2026-10-06T00:40:00.000Z")

    _tick()

    assert [action for action, _params in sent] == [actions.POWER_SCHEDULE]
    assert _get_shift(DEVICE) is not None


# ── 重新分配 ──────────────────────────────────────────────────────────────


def test_on_frame_does_nothing_without_a_row(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_session: object, _device_id: str, req: AssignRequest) -> object:
        assigned.append(req)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", fake_assign)

    extend_hours.on_frame(DEVICE)

    assert assigned == []


def test_on_frame_assigns_back_who_is_missing(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨钟已经把喷头清空了，帧一到就把名单里的人分配回原喷头。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_session: object, _device_id: str, req: AssignRequest) -> object:
        assigned.append((req.door_no, req.cert_no, req.password))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", fake_assign)
    _record_shift(
        DEVICE,
        "2026-10-05T23:50:00.000Z",
        [{"door_no": 3, "cert_no": "2026000001"}, {"door_no": 5, "cert_no": "9999999999"}],
    )

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        # on_frame 同步返回、分配在后台任务里跑；等它做完
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    # 名单里没有的那个学号被跳过，名单里有的那个拿回原来的喷头
    assert assigned == [(3, "2026000001", "000000")]


def test_reassign_skips_someone_already_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """他拨钟之后自己重新开了会话就跳过：放回已成立，不再动。

    "已经成立"的判据是**会话建立于拨钟之后**——只有这样才能与"拨钟前的残留会话"分开
    （见 test_reassign_waits_for_a_leftover_session_from_before_the_shift）。
    """
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:55:00.000Z")
    assigned: list[object] = []

    async def fake_assign(_session: object, _device_id: str, req: AssignRequest) -> object:
        assigned.append(req)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", fake_assign)

    _reassign_with_db(_shift("2026-10-05T23:50:00.000Z"), students_of(CONFIG, "extend_hours_students"))

    assert assigned == []
    assert (DEVICE, "2026000001") in extend_hours._reassigned


def test_reassign_waits_for_a_leftover_session_from_before_the_shift(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """拨钟刚完成时还看得见的旧会话 ≠ "放回已成立"：等浴室端清场，清场后放回。

    2026-10-14 真机序列（门 7）：拨钟成功于 23:48:58.37，而用户会话要到 23:48:59.28
    才被浴室端清掉。`_reassign` 在那 0.9 秒的窗口里被同轮 tick 立即触发——旧代码把仍然
    可见的旧会话当成"他已经有会话 → 放回已成立"永久跳过（静默分支），人整晚没被放回。
    """
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z")  # 建立于拨钟之前：拨钟那一刻他还占着
    assigned: list[object] = []

    async def fake_assign(_session: object, _device_id: str, req: AssignRequest) -> object:
        assigned.append((req.door_no, req.cert_no))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", fake_assign)
    shift = _shift("2026-10-05T23:50:00.000Z")

    # 拨钟后第一次（门还占着）：不分配，且**不标记**——下一次还要再来
    _reassign_with_db(shift, students_of(CONFIG, "extend_hours_students"))
    assert assigned == []
    assert (DEVICE, "2026000001") not in extend_hours._reassigned

    # 浴室端清场之后：放回原喷头，并把这一次记成"已成立"
    _clear_door("2026-10-05T23:50:01.000Z")
    _reassign_with_db(shift, students_of(CONFIG, "extend_hours_students"))
    assert assigned == [(3, "2026000001")]
    assert (DEVICE, "2026000001") in extend_hours._reassigned


# ── 生命周期 ──────────────────────────────────────────────────────────────


def test_background_start_and_stop_are_idempotent(fresh_db: str) -> None:
    """后台任务生命周期已统一到 core/background：重复 start 不叠加，stop 后可再 start。"""

    async def body() -> None:
        from app.core.background import Background

        bg = Background()

        async def noop() -> None:
            return None

        bg.interval("noop", 60.0, noop)
        bg.start()
        first = bg._tasks["noop"]
        bg.start()
        assert bg._tasks["noop"] is first

        await bg.stop()
        assert not bg._tasks

    asyncio.run(body())


def test_every_shift_is_logged_for_the_operator(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """留痕靠两样：动作本身进 command_audit（由命令下发那条路覆盖），决定进日志。

    这一条盯的是后者——`job_run` 的 door_no/cert_no 都非空，装不下设备级的
    事件，所以"为什么动手"只能从日志里读。
    """
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _with_session("2026-10-05T23:40:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))

    _tick()

    assert "extend-hours.shifted" in events


def test_registered_device_is_trusted(fresh_db: str) -> None:
    """建档入口与生产仓储一致：经夹具登记的设备在档案里就是受信任的。"""
    device_link.register_trusted_device(DEVICE)
    from app.db.session import session_scope
    from app.modules.devices import service as devices_service

    with session_scope() as session:
        record = devices_service.get_device(session, DEVICE)
    assert record is not None
    assert record["trusted"]


# ── 半成功与失败路径 ──────────────────────────────────────────────────────


def test_power_failure_after_shift_keeps_the_row_and_retries(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨快之后滚动重布失败：行保留，下一轮续写再试。

    它要单独记一句，而不是并进"拨钟失败"——那会让人以为钟也没动。设备上还挂着上一轮
    的截止，滚动续写断了的话最迟一个窗口后断电，所以失败必须尽快被看见。
    """
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    _record_shift(
        DEVICE,
        shift_rfc3339("2026-10-05T23:50:00.000Z", -ZONE_MINUTES * 60 - 120) or "",
        [{"door_no": 3, "cert_no": "2026000001"}],
    )

    async def half(_session: object, _device_id: str, action: str, _params: object, **_kw: object) -> object:
        if action == actions.POWER_SCHEDULE:
            return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", half)

    _tick()

    assert "extend-hours.power-failed" in events
    row = _get_shift(DEVICE)
    assert row is not None


def test_restore_rearm_failure_keeps_the_row_for_retry(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """拨回成功而新框架重布失败：行保留，下一轮滚动续写 + 重试重布。

    钟已经归位，这一步失败不影响"谁不会被清"——但当晚的断电点还没立起来，必须重试。
    """
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    # shifted_at 是真实轴上的拨钟时刻（10 月 5 日 23:49:30 北京）：午夜已过
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    async def half(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        if action == actions.POWER_SCHEDULE and params.get("off_at") == "23:58":
            return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", half)

    _tick()

    assert "extend-hours.restore-rearm-failed" in events
    row = _get_shift(DEVICE)
    assert row is not None


def test_shift_is_skipped_without_a_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """算不出设备钟就不动它：拿 0 当偏差等于假定那口钟与我们对齐。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent)
    _with_session("2026-10-05T23:40:00.000Z")
    device_clock.clear()

    _tick()

    assert sent == []
    assert _get_shift(DEVICE) is None


def test_on_frame_ignores_a_row_with_nothing_to_assign(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _record_shift(DEVICE, "2026-10-05T23:50:00.000Z", [])

    extend_hours.on_frame(DEVICE)

    assert extend_hours._reassign_tasks == set()


def test_on_frame_needs_a_roster(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setitem(CONFIG, "extend_hours_students", {})
    _record_shift(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    extend_hours.on_frame(DEVICE)

    assert extend_hours._reassign_tasks == set()


def test_reassign_reports_a_failed_command(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令本身失败时，领域层**不抛**、只把结果返回来。

    不看那一句的结果就是记下一条"已分配回去"的假日志，而那个门其实空着。重布之后再没有
    帧可等，也没人会来纠正它——所以这一条断言的是"没有那句假成功"。
    """
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    _record_shift(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    async def failed(_session: object, _device_id: str, _req: AssignRequest) -> dict[str, object]:
        return {"status": "timeout", "reason_code": "server.deadline-exceeded", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", failed)

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
    _record_shift(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    async def refusing(_session: object, _device_id: str, _req: AssignRequest) -> object:
        raise ApiProblemError(DOOR_NOT_FREE, "喷头 3 当前状态：bathing")

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", refusing)

    async def body() -> None:
        extend_hours.on_frame(DEVICE)
        await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert "extend-hours.reassign-waiting" in events
    assert extend_hours._reassign_failed == set()


def test_a_failed_command_is_not_tried_again(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令发了而没成 = 失败，同一个人不再试——每轮再发一遍不会有不同的结果。"""
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    _record_shift(DEVICE, "2026-10-05T23:50:00.000Z", [{"door_no": 3, "cert_no": "2026000001"}])
    attempts: list[int] = []

    async def failed(_session: object, _device_id: str, _req: AssignRequest) -> object:
        attempts.append(1)
        return {"status": "timeout", "reason_code": "server.deadline-exceeded", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", failed)

    async def body() -> None:
        for _ in range(2):
            extend_hours.on_frame(DEVICE)
            await asyncio.gather(*extend_hours._reassign_tasks)

    asyncio.run(body())

    assert len(attempts) == 1


def test_the_failure_memory_is_cleared_when_the_extension_ends(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """调回之后记忆要清：不然下一次延长里那个人会被永久跳过。"""
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", skew_s=SKEW_FAST_S)
    # shifted_at 是真实轴上的拨钟时刻（10 月 5 日 23:49:30 北京）：午夜已过、无人占用
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [])
    extend_hours._reassign_failed.add((DEVICE, "2026000001"))
    extend_hours._reassigned.add((DEVICE, "2026000001"))

    _tick()

    assert _get_shift(DEVICE) is None
    assert extend_hours._reassign_failed == set()
    assert extend_hours._reassigned == set()


# ── 运行时钟校正（延长之外的偏差自愈） ────────────────────────────────────


def test_runtime_correction_fires_when_skewed_outside_extension(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """偏差超阈值且不在延长中：服务端主动下发 clock.set（rearm=true，设备端校钟后
    按电源计划重布 MCU）——设备整夜不重启也能自愈。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)

    _tick()

    # 校正目标是拨回之后的真实表盘时刻（表盘读数减去 15 分钟偏差）
    assert sent == [(actions.CLOCK_SET, {"datetime": "2026-10-06 00:45:00", "rearm": True})]


def test_runtime_correction_skipped_during_extension(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """延长中的偏差是故意的：运行时校正跳过，不与拨快打架。

    真实当地 23:55（午夜未过）：滚动续写照常、校正跳过、恢复被挡。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:55:00.000Z", sent, skew_s=SKEW_FAST_S)
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    _tick()

    assert actions.CLOCK_SET not in [action for action, _params in sent]


def test_runtime_correction_skipped_below_threshold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """偏差在阈值内：不动（秒级噪声不值得一次校钟）。恰好到阈值即算超（`>=`）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=-30.0)

    _tick()

    assert sent == []

    sent.clear()
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=-60.0)

    _tick()

    assert sent == [(actions.CLOCK_SET, {"datetime": "2026-10-06 00:45:00", "rearm": True})]


def test_runtime_correction_skipped_when_disabled(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)
    monkeypatch.setitem(CONFIG, "time_sync_enabled", False)

    _tick()

    assert sent == []


def test_runtime_correction_skipped_while_someone_is_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """有人在占用就不校：校钟的跳变与拨回一样会清掉会话（判据与 `_restore_due` 同源）。

    名单里的人还占着喷头（真实当地 00:45，表盘读 01:00）：校正让位，等他走。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", sent, skew_s=SKEW_FAST_S)
    _with_session("2026-10-06T00:40:00.000Z")

    _tick()

    assert sent == []
    assert _get_shift(DEVICE) is None


# ── 运行时钟校正的失败路径 ────────────────────────────────────────────────


def test_runtime_correction_failure_is_logged(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """校正命令失败：记一句 WARNING，下一轮 tick 重试。"""
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-06T00:45:00.000Z", skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))

    async def failing(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", failing)

    _tick()

    assert "time-sync.correction-failed" in events
    assert "time-sync.corrected" not in events


def test_hhmm_minutes_rejects_garbage(fresh_db: str) -> None:
    assert extend_hours._hhmm_minutes("不是时刻") is None


# ── 补覆盖：内部换算与告警路径 ────────────────────────────────────────────


def test_hhmm_minutes_parses_valid_time(fresh_db: str) -> None:
    assert extend_hours._hhmm_minutes("23:58") == 23 * 60 + 58
    assert extend_hours._hhmm_minutes("00:00") == 0


def test_set_clock_warns_without_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """偏差记录不存在时不动它。"""
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    device_clock.clear()

    ok = _set_clock_with_db(3600, 480)

    assert not ok
    assert "extend-hours.no-skew" in events


def test_set_clock_warns_on_command_failure(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备端 clock.set 失败：记 WARNING 并返回 False。"""
    events: list[str] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))

    async def failing(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "failed", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", failing)

    ok = _set_clock_with_db(3600, 480)

    assert not ok
    assert "extend-hours.clock-failed" in events


def test_roll_power_warns_without_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """滚动重布在偏差记录缺失时不动它。"""
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:50:00.000Z", sent, skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    device_clock.clear()
    _record_shift(DEVICE, "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])

    _tick()

    assert "extend-hours.no-skew" in events
    # 滚动重布失败但行保留（下一轮重试）
    assert _get_shift(DEVICE) is not None


def test_maybe_shift_warns_without_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """到点但偏差记录缺失：不动它。"""
    events: list[str] = []
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z", sent, skew_s=SKEW_FAST_S)
    monkeypatch.setattr(extend_hours, "log_event", lambda _level, event, *_a, **_kw: events.append(event))
    device_clock.clear()
    _with_session("2026-10-05T23:40:00.000Z")

    _tick()

    assert sent == []


def test_tick_advances_each_extending_device_with_its_own_config(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每台延长中的设备用自己的有效配置推进：滚动目标里的上电时刻取自各自的电源计划。

    此前 tick_once 把循环里最后一台的配置传给全部延长的设备——单台时侥幸正确，多设备下
    A 会拿 B 的时区与电源计划续写截止。
    """
    device_link.register_trusted_device("dev-ext-a")
    device_link.register_trusted_device("dev-ext-b")
    base = {
        "extend_hours_enabled": False,  # 不参与拨钟，只走推进
        "extend_hours_zone_offset_minutes": 480,
        "extend_shift_minutes": 15,
        "extend_hours_students": {},
        "time_sync_enabled": False,
        "time_sync_threshold_s": 60,
        "power_off_at": "23:58",
    }
    configs = {
        "dev-ext-a": {**base, "power_on_at": "10:55"},
        "dev-ext-b": {**base, "power_on_at": "08:00"},
    }
    monkeypatch.setattr(config_store, "effective", lambda _session, device_id: configs[device_id])
    server_now = "2026-10-05T16:50:00.000Z"  # 表盘 00:50（UTC+8）
    monkeypatch.setattr(extend_hours, "now_rfc3339", lambda: server_now)
    device_clock.load("dev-ext-a", 0.0)
    device_clock.load("dev-ext-b", 0.0)

    sent: list[tuple[str, str, object]] = []

    async def fake_send(_session: object, device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((device_id, action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", fake_send)

    # 拨钟时刻记为"现在"：恢复条件（过午夜）不成立，推进只做滚动续写
    _record_shift("dev-ext-a", server_now, [])
    _record_shift("dev-ext-b", server_now, [])

    _tick()

    rolls = sorted((item for item in sent if item[1] == actions.POWER_SCHEDULE), key=lambda item: item[0])
    # 表盘 00:50 + 十分钟窗口；上电目标各取各的电源计划
    assert rolls == [
        ("dev-ext-a", actions.POWER_SCHEDULE, {"off_at": "01:00", "on_at": "10:55"}),
        ("dev-ext-b", actions.POWER_SCHEDULE, {"off_at": "01:00", "on_at": "08:00"}),
    ]


def test_tick_clears_the_shift_row_of_a_delisted_device(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """设备不在册（已从设备表除名）时其延长记录随之消失。

    行跟着设备走（外键 CASCADE）：除档那一刻残行就被数据库收走，不会让每轮 tick
    白发一条命令，而那台设备再也不会上电——这一环由外键自己终结。
    """
    sent: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        sent.append((_device_id, _action))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.device_actions, "send_command_and_wait", fake_send)
    _record_shift("dev-gone", "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": "2026000001"}])
    assert _get_shift("dev-gone") is not None

    # 除档：删设备行，延长记录由 CASCADE 带走
    with session_scope() as session:
        from sqlalchemy import delete

        from app.modules.devices.models import Device

        session.execute(delete(Device).where(Device.id == "dev-gone"))
        session.commit()

    _tick()

    assert _get_shift("dev-gone") is None
    assert sent == []


def test_reassign_is_one_shot_per_extension(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """放回原喷头每人只此一次：浴室端 300 秒收回他之后，不再重新预约。

    2026-10-06 深夜实测的循环：放回做成循环时，人走了也被反复重新预约（收回后 1 秒
    又被分配），会话永不空闲、拨回条件永远不成立、滚动重布通宵跑、设备通宵不断电。
    """
    _prepare(monkeypatch, "2026-10-05T23:50:00.000Z")
    students = students_of(CONFIG, "extend_hours_students")
    attempts: list[AssignRequest] = []

    async def fake_assign(_session: object, _device_id: str, req: AssignRequest) -> object:
        attempts.append(req)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(extend_hours.sessions_service, "perform_assign", fake_assign)
    shift = _shift("2026-10-05T23:50:00.000Z")

    # 第一次：他不在占用态，放回
    _reassign_with_db(shift, students)
    assert len(attempts) == 1

    # 已经放回过，不再动
    _reassign_with_db(shift, students)
    assert len(attempts) == 1

    # 他拨钟之后自己重新开了会话，同样算"已成立"：之后哪怕会话结束（他走了），也不再重新预约
    extend_hours._reassigned.discard((DEVICE, "2026000001"))
    _with_session("2026-10-05T23:55:00.000Z")
    _reassign_with_db(shift, students)
    assert len(attempts) == 1
    assert (DEVICE, "2026000001") in extend_hours._reassigned


def test_rolling_rearm_is_throttled_within_the_interval(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """滚动续写限频：武装未满 2 分钟不重复下发。

    窗口 10 分钟、续写 2 分钟，截止永远领先至少 8 分钟——保证不变，命令量降为 1/4。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-10-06T00:50:00.000Z", sent, skew_s=SKEW_FAST_S)
    _record_shift(
        DEVICE,
        shift_rfc3339("2026-10-06T00:50:00.000Z", -ZONE_MINUTES * 60) or "",
        [{"door_no": 3, "cert_no": "2026000001"}],
    )

    _tick()
    _tick()

    assert [action for action, _params in sent].count(actions.POWER_SCHEDULE) == 1
    assert _get_shift(DEVICE) is not None


def _tick() -> None:
    """统一循环一轮；会话边界已收进 tick_once 本体（与生产一致）。"""
    asyncio.run(extend_hours.tick_once())


def _reassign_with_db(shift: "extend_hours.Shift", students: dict[str, str]) -> None:
    from app.db.session import session_scope

    async def body() -> None:
        with session_scope() as session:
            await extend_hours._reassign(session, shift, students)

    asyncio.run(body())


def _set_clock_with_db(seconds: float, zone: int) -> bool:
    from app.db.session import session_scope

    async def body() -> bool:
        with session_scope() as session:
            return await extend_hours._set_clock(session, DEVICE, seconds, zone)

    return asyncio.run(body())
