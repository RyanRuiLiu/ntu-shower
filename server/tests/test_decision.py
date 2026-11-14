"""后台任务的判据：三个功能各自的起算点、次数上限与到点复查。

判据是纯函数，所以这一份不需要数据库、不需要事件循环——每一条都是"给定现态与配置，
该不该动手、什么时候动手"的直接断言。
"""

from dataclasses import replace

from app.models.enums import DoorState
from app.modules.sessions.decision import (
    FEATURE_AUTO_SETTLE,
    FEATURE_RENEW_SESSION,
    FEATURE_RENEW_WAIT,
    Candidate,
    Trigger,
    decide,
    password_of,
    recheck,
    roster,
    still_allowed,
)

#: 基准时刻：判据的全部时刻都是服务端轴的 RFC 3339。
NOW = "2026-10-05T12:00:00.000Z"

BASE_CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": {"2026000001": "000000", "2026000002": "111111"},
    "renew_wait_threshold_s": 270,
    "renew_wait_max_duration_s": 1200,
    "renew_session_enabled": True,
    "renew_session_students": {"2026000001": "000000"},
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": {"2026000001": "000000"},
    "auto_settle_pause_threshold_s": 60,
}


def candidate(**overrides: object) -> Candidate:
    """一个基准候选：没出水的那个学生，在 11:55 被分配。"""
    base = Candidate(
        session_id=7,
        device_id="dev-1",
        door_no=3,
        cert_no="2026000001",
        state=DoorState.WAITING,
        assigned_at="2026-10-05T11:55:00.000Z",
        paused_at="",
        run_started_at="2026-10-05T11:55:00.000Z",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ── 名单与凭证 ──


def test_roster_reads_the_map_and_trims_the_id_only() -> None:
    """学号两端去空格（粘贴常带），**密码不动**——它是凭证，静默改它比留着空格更糟。

    配置页那侧两个都 trim 过了，所以这里只兜住"直接调接口"的情形。
    """
    assert roster({"k": {"1001": "pw"}}, "k") == {"1001": "pw"}
    assert roster({"k": {" 1001 ": "pw"}}, "k") == {"1001": "pw"}
    assert roster({"k": {}}, "k") == {}
    assert roster({}, "k") == {}


def test_roster_tolerates_a_wrong_shape() -> None:
    """名单是配置值，人手改错了不该让整个功能抛异常。"""
    assert roster({"k": "1001,pw"}, "k") == {}
    assert roster({"k": ["1001"]}, "k") == {}


def test_password_of_comes_from_that_tasks_own_roster() -> None:
    """凭证跟着名单走：同一份名单里"在不在"与"有没有密码"是同一件事。

    所以不存在"名单里有他、却没有他的密码"那种静默不执行——代价是同一个学号开两个功能
    要在两份名单里各写一遍。
    """
    assert password_of(BASE_CONFIG, FEATURE_RENEW_WAIT, "2026000001") == "000000"
    assert password_of(BASE_CONFIG, FEATURE_RENEW_WAIT, "2026000002") == "111111"
    # 2026000002 只开了预约续期，所以会话续期那份名单里没有他
    assert password_of(BASE_CONFIG, FEATURE_RENEW_SESSION, "2026000002") == ""
    assert password_of(BASE_CONFIG, FEATURE_AUTO_SETTLE, "9999") == ""


# ── 预约续期 ──


def test_renew_wait_counts_from_assignment_not_from_valve() -> None:
    """起算点是分配时刻：浴室 App 那条 300 秒无水上限也是在它上面走的。

    这里分配在 11:55、现在 12:00：270 秒的到点早已过去（−30），所以给出一条过点的触发。
    """
    trigger = decide(candidate(), BASE_CONFIG, NOW)

    assert trigger == Trigger(FEATURE_RENEW_WAIT, delay_s=-30.0, threshold_s=270)


def test_renew_wait_fires_past_due_with_negative_delay() -> None:
    """已过点也给出一条触发：到点只说明该看一眼了，成不成立由 recheck 判。"""
    trigger = decide(candidate(assigned_at="2026-10-05T11:00:00.000Z"), BASE_CONFIG, NOW)

    assert trigger is not None
    assert trigger.delay_s < 0


def test_renew_wait_stops_at_the_duration_cap() -> None:
    """续够了就停，让浴室端正常释放——他从没出过水，没有账要结。

    上限比的是「这一串第一场到此刻」，不是「续了几次」：NOW 是 12:00，1200 秒的线落在
    11:40。11:47 起步还差七分钟，11:40 与更早就到点了。
    """
    assert decide(candidate(run_started_at="2026-10-05T11:47:00.000Z"), BASE_CONFIG, NOW) is not None
    assert decide(candidate(run_started_at="2026-10-05T11:40:00.000Z"), BASE_CONFIG, NOW) is None
    assert decide(candidate(run_started_at="2026-10-05T11:00:00.000Z"), BASE_CONFIG, NOW) is None


def test_renew_wait_only_applies_while_waiting() -> None:
    """一开阀就不该再"续"了：那是会话续期该管的事。"""
    only_wait = {**BASE_CONFIG, "renew_session_enabled": False, "auto_settle_enabled": False}

    for state in (DoorState.BATHING, DoorState.PAUSED, DoorState.FREE, DoorState.SELECTED):
        assert decide(candidate(state=state), only_wait, NOW) is None


# ── 会话续期 ──


def test_renew_session_applies_only_while_bathing() -> None:
    """只在水还在流时续——关了阀之后的事，归自动结算。

    这里把另外两个功能关掉，好让"没有触发"确实等于"会话续期不适用"，而不是被别的判据顶掉。
    """
    only_renew_session = {**BASE_CONFIG, "renew_wait_enabled": False, "auto_settle_enabled": False}

    for state in (DoorState.PAUSED, DoorState.WAITING, DoorState.FREE, DoorState.SELECTED):
        assert decide(candidate(state=state), only_renew_session, NOW) is None

    trigger = decide(candidate(state=DoorState.BATHING, assigned_at="2026-10-05T11:41:30.000Z"), BASE_CONFIG, NOW)
    assert trigger == Trigger(FEATURE_RENEW_SESSION, delay_s=30.0, threshold_s=1140)


def test_renew_session_has_no_duration_cap() -> None:
    """长期洗的人不该被时长挡住——续期就是为他存在的。"""
    long_run = candidate(state=DoorState.BATHING, run_started_at="2026-10-05T08:00:00.000Z")

    trigger = decide(long_run, BASE_CONFIG, NOW)

    assert trigger is not None
    assert trigger.task == FEATURE_RENEW_SESSION


# ── 自动结算 ──


def test_auto_settle_counts_from_valve_close_not_from_assignment() -> None:
    """暂停从关阀那一刻起算：分配时刻早在几十分钟前，拿它算等于立刻结算。"""
    trigger = decide(candidate(state=DoorState.PAUSED, paused_at="2026-10-05T11:59:30.000Z"), BASE_CONFIG, NOW)

    assert trigger == Trigger(FEATURE_AUTO_SETTLE, delay_s=30.0, threshold_s=60)


def test_auto_settle_needs_a_close_time() -> None:
    """已暂停却没有关阀时刻：那一刻不知道，算不出到点，不判。"""
    only_settle = {**BASE_CONFIG, "renew_session_enabled": False, "renew_wait_enabled": False}

    assert decide(candidate(state=DoorState.PAUSED, paused_at=""), only_settle, NOW) is None


# ── 名单、开关与择早 ──


def test_other_students_are_untouched() -> None:
    """名单是"允许对谁动手"的唯一出处；名单里没有就什么都不做。"""
    assert decide(candidate(cert_no="2026000006"), BASE_CONFIG, NOW) is None


def test_disabled_task_is_not_considered() -> None:
    config = {**BASE_CONFIG, "renew_wait_enabled": False}

    assert decide(candidate(), config, NOW) is None


def test_earliest_trigger_wins() -> None:
    """一个喷头只留一个定时器，所以判据要给出最早的那个。"""
    # 分配 11:55、现在 12:00：会话续期还有 840 秒才到点（1140 − 300）
    trigger = decide(candidate(state=DoorState.BATHING), BASE_CONFIG, NOW)

    assert trigger == Trigger(FEATURE_RENEW_SESSION, delay_s=840.0, threshold_s=1140)


def test_paused_belongs_to_auto_settle_alone() -> None:
    """「已暂停」只有一个主人：自动结算。

    关阀之后再开阀本来就要重新分配，所以这一刻的处置只能是"等够阈值就结算"，不能是
    "再续一场"。
    """
    trigger = decide(candidate(state=DoorState.PAUSED, paused_at="2026-10-05T11:59:00.000Z"), BASE_CONFIG, NOW)

    assert trigger == Trigger(FEATURE_AUTO_SETTLE, delay_s=0.0, threshold_s=60)


def test_a_session_nearly_full_that_just_closed_the_valve_still_settles() -> None:
    """真机上的那一例：会话快满 20 分钟时关阀，处置仍然是结算，不是续期。

    修复前这里是"续期先动手"——续期的到点正在此刻、结算还要 50 秒，两条判据都认领
    `PAUSED` 而取最早的那条。于是洗完关阀的会话被续成下一场：喷头不释放，延长服务时间也
    因为"还有未结束的会话"迟迟不拨回（真机是拖到 00:33 才拨回）。钉住它，是为了这条边界
    不再被"续期优先"改回去。
    """
    just_closed = candidate(
        state=DoorState.PAUSED,
        assigned_at="2026-10-05T11:41:00.000Z",  # 续期的到点正在此刻
        paused_at="2026-10-05T11:59:50.000Z",  # 结算还要 50 秒
    )

    assert decide(just_closed, BASE_CONFIG, NOW) == Trigger(FEATURE_AUTO_SETTLE, delay_s=50.0, threshold_s=60)


# ── 到点复查 ──


def test_recheck_passes_when_nothing_moved() -> None:
    expected = candidate()
    trigger = Trigger(FEATURE_RENEW_WAIT, delay_s=0.0, threshold_s=270)

    assert recheck(expected, expected, trigger, BASE_CONFIG, NOW) is None


def test_recheck_refuses_when_the_duration_cap_ran_out_while_the_timer_waited() -> None:
    """挂表时还没到上限、到点已经超了——必须放弃这一次。

    真机上发生过：`renew_wait` 的表在会话跑了 19 分钟时挂上（上限 20 分钟），50 秒后到点
    执行，此时已 23 分钟，却照样结算并分配了一次。只比对"现态变没变"看不见"上限用尽"
    （它不在现态的字段里），所以到点要把适用性整条重算。
    """
    armed = candidate(run_started_at="2026-10-05T11:41:00.000Z")  # 12:01:00 才到 1200 秒
    trigger = Trigger(FEATURE_RENEW_WAIT, delay_s=60.0, threshold_s=270)
    fired_late = "2026-10-05T12:05:00.000Z"  # 到点了，而这一串已经跑了 24 分钟

    assert recheck(armed, armed, trigger, BASE_CONFIG, NOW) is None
    assert recheck(armed, armed, trigger, BASE_CONFIG, fired_late) == "判据已不适用（起算点为空或时长上限用尽）"


def test_recheck_reports_every_kind_of_change() -> None:
    expected = candidate()
    trigger = Trigger(FEATURE_RENEW_WAIT, delay_s=0.0, threshold_s=270)

    assert recheck(None, expected, trigger, BASE_CONFIG, NOW) == "会话已结束"
    assert recheck(candidate(session_id=8), expected, trigger, BASE_CONFIG, NOW) == "换了会话"
    assert recheck(candidate(cert_no="2026000002"), expected, trigger, BASE_CONFIG, NOW) == "换了人"
    assert recheck(candidate(state=DoorState.BATHING), expected, trigger, BASE_CONFIG, NOW) == (
        "状态已变（waiting → bathing）"
    )
    assert recheck(expected, expected, trigger, {**BASE_CONFIG, "renew_wait_enabled": False}, NOW) == "功能已关"
    assert recheck(expected, expected, trigger, {**BASE_CONFIG, "renew_wait_students": {}}, NOW) == "不在名单里"


def test_still_allowed_is_the_same_question_asked_before_sending() -> None:
    """复查到下单之间隔着一次取数与一次排队，所以下单前要再问一次。"""
    assert still_allowed("2026000001", FEATURE_RENEW_WAIT, BASE_CONFIG) is None
    assert still_allowed("2026000001", FEATURE_AUTO_SETTLE, BASE_CONFIG) is None
    assert still_allowed("9999", FEATURE_RENEW_WAIT, BASE_CONFIG) == "不在名单里"
