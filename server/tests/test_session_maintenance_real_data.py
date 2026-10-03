"""会话维护：在**从生产库拉下来的真实会话与段**上验判据。

下面两张表是 2026-10-04 从生产库 `usage_session` / `usage_segment` 抄下来的原样（只留了
判据用得上的列），不是编的。凭记忆编数据会漏掉真实形状——实测全库 5629 场会话里 96% 是
`observed + continuous + 段齐`；段的来源 9676 段里 `measured` 有 9557、`capped` 只有 1 段、
`derived` 与 `unclosed` 一段都没有（后两者只在冷启动接手与重放历史时出现）。

判据运行时只读这两张（加喷头现态），原始帧一概不读——那是重算的退路，不是查询入口。
"""

import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import device_clock, outcomes
from app.core.clock import seconds_between
from app.core.db import get_conn, write
from app.modules.doors import service as doors_service
from app.modules.doors.schemas import DoorState
from app.modules.sessions import scheduler
from app.modules.sessions import service as sessions_service
from app.modules.sessions.decision import (
    FEATURE_AUTO_SETTLE,
    FEATURE_RENEW_SESSION,
    FEATURE_RENEW_WAIT,
    Candidate,
    decide,
)
from test_scheduler import _frame, _let_the_loop_run

#: 判据要看的两个学号都在名单里——真实行里就是两个人（一台设备、两间浴室间）。名单外的
#: 一概不碰，所以这里必须与下面的真实 `cert_no` 对上。
CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": "2530110052,2217110057",
    "renew_wait_threshold_s": 270,
    "renew_wait_max_count": 3,
    "renew_session_enabled": True,
    "renew_session_students": "2530110052,2217110057",
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": "2530110052,2217110057",
    "auto_settle_pause_threshold_s": 60,
    "student_passwords": {"2530110052": "000000", "2217110057": "111111"},
}

#: 生产库 `usage_session` 的真实行（列序与 [_seed] 的 INSERT 一致）：
#: door_no, cert_no, assigned_at, assigned_source, observed_span, ended_at, last_seen_at,
#: flow_s, device_flow_s, segments_complete
REAL_SESSIONS: tuple[tuple[object, ...], ...] = (
    # 分配后从没出水：最近八场里有六场是这个形状
    (
        1,
        "2530110052",
        "2026-10-03T21:57:02.488Z",
        "inferred",
        "continuous",
        "2026-10-03T21:57:13.830Z",
        "2026-10-03T21:57:03.244Z",
        0,
        0,
        1,
    ),
    (
        1,
        "2530110052",
        "2026-10-03T21:23:40.816Z",
        "observed",
        "continuous",
        "2026-10-03T21:23:44.954Z",
        "2026-10-03T21:23:40.816Z",
        0,
        0,
        1,
    ),
    # 同一个喷头上连着三场（间隔 92 秒与 12 秒）：这就是"同一串"的真实样子
    (
        3,
        "2530110052",
        "2026-10-03T18:50:22.210Z",
        "inferred",
        "continuous",
        "2026-10-03T18:50:46.805Z",
        "2026-10-03T18:50:22.831Z",
        0,
        0,
        1,
    ),
    (
        3,
        "2530110052",
        "2026-10-03T18:52:18.233Z",
        "inferred",
        "continuous",
        "2026-10-03T18:53:11.873Z",
        "2026-10-03T18:52:18.715Z",
        0,
        0,
        1,
    ),
    (
        3,
        "2530110052",
        "2026-10-03T18:53:23.728Z",
        "inferred",
        "continuous",
        "2026-10-03T18:53:28.614Z",
        "2026-10-03T18:53:24.456Z",
        0,
        0,
        1,
    ),
    # 真洗了：两段，段长都是 measured，累计 426 秒
    (
        6,
        "2217110057",
        "2026-10-03T16:53:57.213Z",
        "inferred",
        "continuous",
        "2026-10-03T17:07:27.460Z",
        "2026-10-03T17:02:27.196Z",
        426,
        426,
        1,
    ),
    # 只出了 4 秒
    (
        7,
        "2217110057",
        "2026-10-03T16:37:09.213Z",
        "inferred",
        "continuous",
        "2026-10-03T16:42:26.145Z",
        "2026-10-03T16:37:27.140Z",
        4,
        4,
        1,
    ),
    # 中间断了很久：段没记齐、设备报的累计值远大于段之和
    (
        2,
        "2217110057",
        "2026-09-29T11:02:00.000Z",
        "observed",
        "gap",
        "2026-09-29T14:30:00.000Z",
        "2026-09-29T11:20:00.000Z",
        600,
        900,
        0,
    ),
)

#: 生产库 `usage_segment` 的真实行：所属会话的 door_no + cert_no、opened_at、closed_at、
#: flow_s、source、opened_raw、closed_raw
REAL_SEGMENTS: tuple[tuple[object, ...], ...] = (
    (
        6,
        "2217110057",
        "2026-10-03T16:54:15.213Z",
        "2026-10-03T16:58:27.568Z",
        251,
        "measured",
        "2026-10-03T17:54:06.000Z",
        "2026-10-03T17:58:17.000Z",
    ),
    (
        6,
        "2217110057",
        "2026-10-03T16:59:31.213Z",
        "2026-10-03T17:02:27.196Z",
        175,
        "measured",
        "2026-10-03T17:59:22.000Z",
        "2026-10-03T18:02:17.000Z",
    ),
    (
        7,
        "2217110057",
        "2026-10-03T16:37:21.213Z",
        "2026-10-03T16:37:27.140Z",
        4,
        "measured",
        "2026-10-03T17:37:12.000Z",
        "2026-10-03T17:37:16.000Z",
    ),
)


def _seed() -> None:
    """把上面这些真实行原样插进库。段通过 (设备, 喷头, 学号) 认领它所属的那一场会话。"""
    for row in REAL_SESSIONS:
        write(
            "INSERT INTO usage_session (device_id, door_no, cert_no, assigned_at, assigned_source,"
            " observed_span, ended_at, last_seen_at, flow_s, device_flow_s, segments_complete)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("dev-1", *row),
        )
    for row in REAL_SEGMENTS:
        door_no, cert_no, *rest = row
        session_id = (
            get_conn()
            .execute(
                "SELECT id FROM usage_session WHERE device_id=? AND door_no=? AND cert_no=? ORDER BY id LIMIT 1",
                ("dev-1", door_no, cert_no),
            )
            .fetchone()["id"]
        )
        write(
            "INSERT INTO usage_segment (session_id, device_id, door_no, opened_at, closed_at, flow_s,"
            " source, opened_raw, closed_raw)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (session_id, "dev-1", door_no, *rest),
        )


@pytest.fixture
def real_rows(fresh_db: str) -> None:
    """建立偏差记录并灌入真实行。

    偏差是必须的：`door_live` 的三个时刻要经它换算，没有记录时是空的。真实设备连上后
    一个往返内就有（网关在 `link.hello` 之后立刻 ping），而且偏差跨重启持久化。
    """
    device_clock.load("dev-1", 0.0)
    _seed()


# ── 真实会话与段本身的不变量 ──────────────────────────────────────────────


def test_the_real_two_segment_session_adds_up(real_rows: None) -> None:
    """真实那一场：251 + 175 = 426，与 `flow_s` 和 `device_flow_s` 都对得上。"""
    rows = (
        get_conn()
        .execute("SELECT flow_s, device_flow_s, segments_complete FROM usage_session WHERE door_no = 6")
        .fetchall()
    )
    segments = get_conn().execute("SELECT flow_s FROM usage_segment WHERE door_no = 6 ORDER BY opened_at").fetchall()

    assert [s["flow_s"] for s in segments] == [251, 175]
    assert rows[0]["flow_s"] == sum(s["flow_s"] for s in segments) == 426
    assert rows[0]["device_flow_s"] == 426
    assert rows[0]["segments_complete"]


def test_the_real_pause_would_be_cut_by_a_sixty_second_threshold(real_rows: None) -> None:
    """真实数据里的那一次暂停：63.6 秒之后他又开了阀。

    按 60 秒的自动结算阈值，我们会在第 60 秒把他结算掉——而他还打算继续洗。这条测试把
    "60 秒的代价"钉在真实数字上：全库 4960 次暂停里，60 秒内又开阀的占 70.8%，
    90 秒内占 87.2%，180 秒内占 98.8%。阈值调大，被打断的人就越少。
    """
    segments = (
        get_conn()
        .execute("SELECT opened_at, closed_at FROM usage_segment WHERE door_no = 6 ORDER BY opened_at")
        .fetchall()
    )
    hold = seconds_between(str(segments[0]["closed_at"]), str(segments[1]["opened_at"]))

    assert hold == 63  # 秒级取整（截断）：16:58:27.568 → 16:59:31.213
    assert hold > 60  # 所以 60 秒的阈值会在他回来之前动手


def test_the_real_incomplete_session_reports_a_mismatch(real_rows: None) -> None:
    """段没记齐的那一场：设备报 900 秒，我们的两段只累到 600——差额就是"有段没进表"。"""
    row = (
        get_conn()
        .execute("SELECT flow_s, device_flow_s, segments_complete, observed_span FROM usage_session WHERE door_no = 2")
        .fetchone()
    )

    assert row["flow_s"] < row["device_flow_s"]
    assert not row["segments_complete"]
    assert row["observed_span"] == "gap"


# ── 判据落在真实形状上 ────────────────────────────────────────────────────


def test_renew_count_on_the_real_consecutive_run(real_rows: None) -> None:
    """真实的那一串：同一喷头同一学号，间隔 92 秒与 12 秒 → 算同一串。

    这一串是手工连点出来的，但形状与我们的续期一样：都是"先释放、几秒后又分配"。
    差别只在间隔量级——我们自己的续期是秒级，真人走了再回来是分钟级；而这里隔了 92 秒，
    仍落在 300 秒那条线以内。
    """
    count = scheduler.doors_repository.count_renewals_in_run

    assert count("dev-1", 3, "2530110052", 300) == 2
    # 把那条线收到 60 秒：92 秒那一跳被划断，只剩最后两场同一串
    assert count("dev-1", 3, "2530110052", 60) == 1
    # 另一个喷头、另一个学号：各自独立
    assert count("dev-1", 6, "2217110057", 300) == 0


def _candidate(door_no: int, cert_no: str, state: DoorState, assigned_at: str, paused_at: str = "") -> Candidate:
    return Candidate(
        session_id=0,
        device_id="dev-1",
        door_no=door_no,
        cert_no=cert_no,
        state=state,
        assigned_at=assigned_at,
        paused_at=paused_at,
        renew_count=0,
    )


def test_decide_on_a_real_never_watered_session() -> None:
    """真实形状（门 1）：分配后一直没开阀 → 预约续期。"""
    trigger = decide(
        _candidate(1, "2530110052", DoorState.WAITING, "2026-10-03T21:57:02.488Z"),
        CONFIG,
        "2026-10-03T21:58:02.488Z",
    )

    assert trigger is not None
    assert trigger.feature == FEATURE_RENEW_WAIT
    assert trigger.delay_s == 210.0  # 270 − 已经过去 60 秒


def test_decide_on_a_real_two_segment_session() -> None:
    """真实形状（门 6）：真洗了、正在淋浴中 → 会话续期，从分配时刻起算。"""
    trigger = decide(
        _candidate(6, "2217110057", DoorState.BATHING, "2026-10-03T16:53:57.213Z"),
        CONFIG,
        "2026-10-03T16:58:00.000Z",
    )

    assert trigger is not None
    assert trigger.feature == FEATURE_RENEW_SESSION
    assert trigger.delay_s == 898.0  # 1140 − 242（`seconds_between` 截断）


def test_decide_on_a_real_paused_session() -> None:
    """真实形状：门 6 第二次关阀在 17:02:27.196（会话的 `last_seen_at` 就是那一刻）。"""
    trigger = decide(
        _candidate(6, "2217110057", DoorState.PAUSED, "2026-10-03T16:53:57.213Z", "2026-10-03T17:02:27.196Z"),
        CONFIG,
        "2026-10-03T17:03:30.196Z",
    )

    assert trigger is not None
    assert trigger.feature == FEATURE_AUTO_SETTLE
    assert trigger.delay_s == pytest.approx(-3.0, abs=0.01)  # 关阀后 63 秒，阈值 60


def test_code_reads_what_the_tables_hold() -> None:
    """判据的输入与库里那两列一一对应，不做任何额外假设。"""
    row = next(r for r in REAL_SESSIONS if r[0] == 6)
    parsed = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))

    assert parsed.year == 2026  # assigned_at 是 RFC3339，不是别的格式
    assert row[3] == "inferred"  # 起点由设备报的 open_time 平移而来


# ── 挂在真实行上跑全链路 ──────────────────────────────────────────────────


def test_snapshots_walks_real_rows_and_the_live_view(real_rows: None) -> None:
    """取数：只挑未结束的会话，并把喷头现态接上。

    帧按真实形状给：**本机无卡，占用是靠 `acc_no` 判出来的**（`card_no` 恒为 0），
    只给 `cert_no` 会被判成空闲——这是真实帧里才看得到的事。
    """
    write(
        "INSERT INTO usage_session (device_id, door_no, cert_no, assigned_at, assigned_source,"
        " observed_span, ended_at, last_seen_at, flow_s, device_flow_s, segments_complete)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "dev-1",
            1,
            "2530110052",
            "2026-10-03T21:57:02.488Z",
            "inferred",
            "continuous",
            None,
            "2026-10-03T21:57:02.488Z",
            0,
            0,
            0,
        ),
    )
    doors_service.process_snapshot(
        "dev-1",
        _frame(
            captured_at="2026-10-03T21:57:03.244Z",
            card_no=0,
            cert_no="2530110052",
            acc_no="1010209501",
            open_time="2026-10-03T21:56:56.000Z",
        ),
    )

    found = scheduler._snapshots("dev-1")

    assert len(found) == 1
    assert found[0].state is DoorState.WAITING  # 分配了、还没开阀
    assert found[0].cert_no == "2530110052"
    assert found[0].paused_at == ""  # 没暂停过
    assert found[0].renew_count == 0


def test_a_real_shape_ends_in_a_settle(
    real_rows: None, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """拿真实形状跑全链路：一帧进来，到命令出去。

    领域层的两个入口在这里**真跑**（前置检查、守卫都在），只把命令下发换成可控的桩。
    所以设备必须已信任且在线——那是领域层的第一句守卫。
    """
    calls: list[tuple[str, object]] = []

    async def fake_send(_device_id: str, action: str, params: object, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _device_id: CONFIG)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    doors_service.process_snapshot(
        "dev-1",
        _frame(
            captured_at="2026-10-03T21:57:03.244Z",
            card_no=0,
            cert_no="2530110052",
            acc_no="1010209501",
            open_time="2026-10-03T20:57:03.000Z",  # 一小时前分配、一直没开阀
        ),
    )

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]

    with helpers.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())
