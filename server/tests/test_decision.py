"""后台任务的判据：三个功能各自的起算点、次数上限与到点复查。

判据是纯函数，所以这一份不需要数据库、不需要事件循环——每一条都是"给定现态与配置，
该不该动手、什么时候动手"的直接断言。
"""

from dataclasses import replace

from app.modules.doors.schemas import DoorState
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
    "renew_wait_students": {"99990052": "000000", "99990053": "111111"},
    "renew_wait_threshold_s": 270,
    "renew_wait_max_count": 3,
    "renew_session_enabled": True,
    "renew_session_students": {"99990052": "000000"},
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": {"99990052": "000000"},
    "auto_settle_pause_threshold_s": 60,
}


def candidate(**overrides: object) -> Candidate:
    """一个基准候选：没出水的那个学生，在 11:55 被分配。"""
    base = Candidate(
        session_id=7,
        device_id="dev-1",
        door_no=3,
        cert_no="99990052",
        state=DoorState.WAITING,
        assigned_at="2026-10-05T11:55:00.000Z",
        paused_at="",
        renew_count=0,
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


def test_password_of_comes_from_that_features_own_roster() -> None:
    """凭证跟着名单走：同一份名单里"在不在"与"有没有密码"是同一件事。

    所以不存在"名单里有他、却没有他的密码"那种静默不执行——代价是同一个学号开两个功能
    要在两份名单里各写一遍。
    """
    assert password_of(BASE_CONFIG, FEATURE_RENEW_WAIT, "99990052") == "000000"
    assert password_of(BASE_CONFIG, FEATURE_RENEW_WAIT, "99990053") == "111111"
    # 99990053 只开了预约续期，所以会话续期那份名单里没有他
    assert password_of(BASE_CONFIG, FEATURE_RENEW_SESSION, "99990053") == ""
    assert password_of(BASE_CONFIG, FEATURE_AUTO_SETTLE, "9999") == ""


# ── 预约续期 ──


def test_renew_wait_counts_from_assignment_not_from_valve() -> None:
    """起算点是分配时刻：浴室 App 那条 300 秒无水上限也是在它上面走的。

    这里分配在 11:55、现在 12:00：270 秒的到点早已过去（−30），所以给出一条过点的触发。
    """
    trigger = decide(candidate(), BASE_CONFIG, NOW)

    assert trigger == Trigger(feature=FEATURE_RENEW_WAIT, delay_s=-30.0)


def test_renew_wait_fires_past_due_with_negative_delay() -> None:
    """已过点也给出一条触发：到点只说明该看一眼了，成不成立由 recheck 判。"""
    trigger = decide(candidate(assigned_at="2026-10-05T11:00:00.000Z"), BASE_CONFIG, NOW)

    assert trigger is not None
    assert trigger.delay_s < 0


def test_renew_wait_stops_at_the_count_cap() -> None:
    """续满之后停止续期，让浴室端正常释放——他从没出过水，没有账要结。"""
    assert decide(candidate(renew_count=2), BASE_CONFIG, NOW) is not None
    assert decide(candidate(renew_count=3), BASE_CONFIG, NOW) is None
    assert decide(candidate(renew_count=9), BASE_CONFIG, NOW) is None


def test_renew_wait_only_applies_while_waiting() -> None:
    """一开阀就不该再"续"了：那是会话续期该管的事。"""
    only_wait = {**BASE_CONFIG, "renew_session_enabled": False, "auto_settle_enabled": False}

    for state in (DoorState.BATHING, DoorState.PAUSED, DoorState.FREE, DoorState.SELECTED):
        assert decide(candidate(state=state), only_wait, NOW) is None


# ── 会话续期 ──


def test_renew_session_applies_to_both_occupied_states() -> None:
    for state in (DoorState.BATHING, DoorState.PAUSED):
        trigger = decide(candidate(state=state, assigned_at="2026-10-05T11:41:30.000Z"), BASE_CONFIG, NOW)
        assert trigger == Trigger(feature=FEATURE_RENEW_SESSION, delay_s=30.0)


def test_renew_session_has_no_count_cap() -> None:
    """长期洗的人不该被次数挡住——续期就是为他存在的。"""
    trigger = decide(candidate(state=DoorState.BATHING, renew_count=99), BASE_CONFIG, NOW)

    assert trigger is not None
    assert trigger.feature == FEATURE_RENEW_SESSION


# ── 自动结算 ──


def test_auto_settle_counts_from_valve_close_not_from_assignment() -> None:
    """暂停从关阀那一刻起算：分配时刻早在几十分钟前，拿它算等于立刻结算。"""
    trigger = decide(candidate(state=DoorState.PAUSED, paused_at="2026-10-05T11:59:30.000Z"), BASE_CONFIG, NOW)

    assert trigger == Trigger(feature=FEATURE_AUTO_SETTLE, delay_s=30.0)


def test_auto_settle_needs_a_close_time() -> None:
    """已暂停却没有关阀时刻：那一刻不知道，算不出到点，不判。"""
    only_settle = {**BASE_CONFIG, "renew_session_enabled": False, "renew_wait_enabled": False}

    assert decide(candidate(state=DoorState.PAUSED, paused_at=""), only_settle, NOW) is None


# ── 名单、开关与择早 ──


def test_other_students_are_untouched() -> None:
    """名单是"允许对谁动手"的唯一出处；名单里没有就什么都不做。"""
    assert decide(candidate(cert_no="2530999999"), BASE_CONFIG, NOW) is None


def test_disabled_feature_is_not_considered() -> None:
    config = {**BASE_CONFIG, "renew_wait_enabled": False}

    assert decide(candidate(), config, NOW) is None


def test_earliest_trigger_wins() -> None:
    """一个喷头只留一个定时器，所以判据要给出最早的那个。"""
    # 分配 11:55、现在 12:00：会话续期还有 840 秒才到点（1140 − 300）
    trigger = decide(candidate(state=DoorState.BATHING), BASE_CONFIG, NOW)

    assert trigger == Trigger(feature=FEATURE_RENEW_SESSION, delay_s=840.0)


def test_paused_is_claimed_by_two_features_and_renewal_wins() -> None:
    """「已暂停」同时落进会话续期与自动结算，起算点不同，取最早的那条。

    这里关阀在 11:59（60 秒后到点＝0），而会话已走了 5 分钟（续期还早）——所以
    自动结算先动手。反过来的那一半（会话快满 20 分钟才关阀、续期先动手）由
    [test_renewal_beats_settle_when_the_session_is_nearly_full] 钉住。
    """
    trigger = decide(candidate(state=DoorState.PAUSED, paused_at="2026-10-05T11:59:00.000Z"), BASE_CONFIG, NOW)

    assert trigger == Trigger(feature=FEATURE_AUTO_SETTLE, delay_s=0.0)


def test_renewal_beats_settle_when_the_session_is_nearly_full() -> None:
    """会话快满 20 分钟时才关阀：续期先动手，保住他这一场比结束它更保守。"""
    nearly_full = candidate(
        state=DoorState.PAUSED,
        assigned_at="2026-10-05T11:41:00.000Z",  # 1140 秒在 12:00:00 到点
        paused_at="2026-10-05T11:59:50.000Z",  # 结算还要 50 秒
    )

    trigger = decide(nearly_full, BASE_CONFIG, NOW)

    assert trigger == Trigger(feature=FEATURE_RENEW_SESSION, delay_s=0.0)


# ── 到点复查 ──


def test_recheck_passes_when_nothing_moved() -> None:
    expected = candidate()
    assert recheck(expected, expected, Trigger(FEATURE_RENEW_WAIT, 0.0), BASE_CONFIG) is None


def test_recheck_reports_every_kind_of_change() -> None:
    expected = candidate()
    trigger = Trigger(FEATURE_RENEW_WAIT, 0.0)

    assert recheck(None, expected, trigger, BASE_CONFIG) == "会话已结束"
    assert recheck(candidate(session_id=8), expected, trigger, BASE_CONFIG) == "换了会话"
    assert recheck(candidate(cert_no="99990053"), expected, trigger, BASE_CONFIG) == "换了人"
    assert recheck(candidate(state=DoorState.BATHING), expected, trigger, BASE_CONFIG) == (
        "状态已变（waiting → bathing）"
    )
    assert recheck(expected, expected, trigger, {**BASE_CONFIG, "renew_wait_enabled": False}) == "功能已关"
    assert recheck(expected, expected, trigger, {**BASE_CONFIG, "renew_wait_students": {}}) == "不在名单里"


def test_still_allowed_is_the_same_question_asked_before_sending() -> None:
    """复查到下单之间隔着一次取数与一次排队，所以下单前要再问一次。"""
    assert still_allowed("99990052", FEATURE_RENEW_WAIT, BASE_CONFIG) is None
    assert still_allowed("99990052", FEATURE_AUTO_SETTLE, BASE_CONFIG) is None
    assert still_allowed("9999", FEATURE_RENEW_WAIT, BASE_CONFIG) == "不在名单里"
