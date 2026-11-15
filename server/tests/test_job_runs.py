"""后台任务：在**从生产库拉下来的真实会话与段**上验判据。

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
from tests import device_link, door_link

from app.contracts import DoorState
from app.core import device_clock, outcomes
from app.core.clock import now_rfc3339, seconds_between
from app.db.session import session_scope
from app.modules.devices import actions as device_actions
from app.modules.sessions import scheduler
from app.modules.sessions.decision import (
    JOB_AUTO_SETTLE,
    JOB_RENEW_SESSION,
    JOB_RENEW_WAIT,
    Candidate,
    decide,
)
from test_scheduler import _frame, _let_the_loop_run

#: 生产库 `usage_session` 的真实行（列序与 [_seed] 的 INSERT 一致）：
#: door_no, cert_no, assigned_at, assigned_source, observed_span, ended_at, last_seen_at,
#: flow_s, device_flow_s, segments_complete
REAL_SESSIONS: tuple[tuple[object, ...], ...] = (
    # 分配后从没出水：最近八场里有六场是这个形状
    (
        1,
        "2026000001",
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
        "2026000001",
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
        "2026000001",
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
        "2026000001",
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
        "2026000001",
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
        "2026000002",
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
        "2026000002",
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
        "2026000002",
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

#: 最老一场分配的时刻：时长上限以它为基准（见下）。
_OLDEST_ASSIGNED_AT = min(row[2] for row in REAL_SESSIONS)

#: 判据要看的两个学号都在名单里——真实行里就是两个人（一台设备、两间浴室间）。名单外的
#: 一概不碰，所以这里必须与下面的真实 `cert_no` 对上。
#:
#: 时长上限跟着真实行的年龄走：真实行的日子是写死的，随真实日期漂移只会越来越老，
#: 固定的上限迟早被"现在"追上（2026-10-05 就追上过一次：行的年龄跨过了当时的
#: 86400 秒上限，判据停手、测到的是"没动手"）。上限取"最老一场分配到此刻"的距离
#: 再加一小时余量，每次导入重算——行龄以天计，余量以小时计，永远够用。
#: 上限本身由 test_decision.py 盯。
_RUN_AGE_S = seconds_between(_OLDEST_ASSIGNED_AT, now_rfc3339()) + 3600
CONFIG: dict[str, object] = {
    "renew_wait_enabled": True,
    "renew_wait_students": {"2026000001": "000000", "2026000002": "111111"},
    "renew_wait_threshold_s": 270,
    "renew_wait_max_duration_s": _RUN_AGE_S,
    "renew_session_enabled": True,
    "renew_session_students": {"2026000001": "000000", "2026000002": "111111"},
    "renew_session_threshold_s": 1140,
    "auto_settle_enabled": True,
    "auto_settle_students": {"2026000001": "000000", "2026000002": "111111"},
    "auto_settle_pause_threshold_s": 60,
}

#: 最老一场分配到此刻的距离（秒），加一小时余量后作为 renew_wait 的时长上限。

#: 生产库 `usage_segment` 的真实行：所属会话的 door_no + cert_no、opened_at、closed_at、
#: flow_s、source、opened_raw、closed_raw
REAL_SEGMENTS: tuple[tuple[object, ...], ...] = (
    (
        6,
        "2026000002",
        "2026-10-03T16:54:15.213Z",
        "2026-10-03T16:58:27.568Z",
        251,
        "measured",
        "2026-10-03T17:54:06.000Z",
        "2026-10-03T17:58:17.000Z",
    ),
    (
        6,
        "2026000002",
        "2026-10-03T16:59:31.213Z",
        "2026-10-03T17:02:27.196Z",
        175,
        "measured",
        "2026-10-03T17:59:22.000Z",
        "2026-10-03T18:02:17.000Z",
    ),
    (
        7,
        "2026000002",
        "2026-10-03T16:37:21.213Z",
        "2026-10-03T16:37:27.140Z",
        4,
        "measured",
        "2026-10-03T17:37:12.000Z",
        "2026-10-03T17:37:16.000Z",
    ),
)


def _read_snapshots(device_id: str) -> list:
    """直读 _snapshots 的短会话包装。"""
    with session_scope() as session:
        return scheduler._snapshots(session, device_id)


def _moment(text: str) -> datetime:
    """RFC 3339 文本 → UTC datetime（模型列的落库形状）。"""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _device_pk() -> int | None:
    from sqlalchemy import select

    from app.modules.devices.models import Device

    with session_scope() as session:
        return session.scalar(select(Device.id).where(Device.id == "dev-1"))


def _seed() -> None:
    """把上面这些真实行原样插进库。段通过 (设备, 喷头, 学号) 认领它所属的那一场会话。"""
    from app.modules.doors.models import DoorSegment, DoorSession

    pk = _device_pk()
    ids_by_key: dict[tuple[int, str], int] = {}
    for row in REAL_SESSIONS:
        (
            door_no,
            cert_no,
            assigned_at,
            assigned_source,
            observed_span,
            ended_at,
            last_seen_at,
            flow_s,
            device_flow_s,
            complete,
        ) = row
        orm_row = DoorSession(
            device_id=pk,
            door_no=door_no,
            cert_no=cert_no,
            assigned_at=_moment(assigned_at),
            assigned_source=assigned_source,
            observed_span=observed_span,
            ended_at=_moment(ended_at) if ended_at else None,
            last_seen_at=_moment(last_seen_at),
            total_flow_s=flow_s,
            device_flow_s=device_flow_s,
            segments_complete=bool(complete),
        )
        with session_scope() as session:
            session.add(orm_row)
            session.commit()
            session.flush()
        ids_by_key[(int(door_no), str(cert_no))] = int(orm_row.id)
    for row in REAL_SEGMENTS:
        door_no, cert_no, *rest = row
        (opened_at, closed_at, flow_s, source, opened_raw, closed_raw) = rest
        with session_scope() as session:
            session.add(
                DoorSegment(
                    session_id=ids_by_key[(int(door_no), str(cert_no))],
                    device_id=pk,
                    door_no=door_no,
                    opened_at=_moment(opened_at),
                    closed_at=_moment(closed_at) if closed_at else None,
                    flow_s=flow_s,
                    source=source,
                    opened_raw=opened_raw,
                    closed_raw=closed_raw,
                )
            )
            session.commit()


@pytest.fixture
def real_rows(fresh_db: str) -> None:
    """建立偏差记录并灌入真实行。

    偏差是必须的：`door_live` 的三个时刻要经它换算，没有记录时是空的。真实设备连上后
    一个往返内就有（网关在 `link.hello` 之后立刻 ping），而且偏差跨重启持久化。
    """
    device_clock.load("dev-1", 0.0)
    # 新层的行经外键挂设备：播种前先登记（与线上握手先行的次序一致）
    device_link.register_trusted_device("dev-1")
    _seed()


# ── 真实会话与段本身的不变量 ──────────────────────────────────────────────


def test_the_real_two_segment_session_adds_up(real_rows: None) -> None:
    """真实那一场：251 + 175 = 426，与 `flow_s` 和 `device_flow_s` 都对得上。"""
    rows = [row for row in door_link.list_sessions("dev-1") if row["door_no"] == 6]
    segments = [seg for row in rows for seg in door_link.list_segments_of(row["id"])]

    assert [seg["flow_s"] for seg in segments] == [251, 175]
    assert rows[0]["total_flow_s"] == sum(seg["flow_s"] for seg in segments) == 426
    assert rows[0]["device_flow_s"] == 426
    assert rows[0]["segments_complete"] is True


def test_the_real_pause_would_be_cut_by_a_sixty_second_threshold(real_rows: None) -> None:
    """真实数据里的那一次暂停：63.6 秒之后他又开了阀。

    按 60 秒的自动结算阈值，我们会在第 60 秒把他结算掉——而他还打算继续洗。这条测试把
    "60 秒的代价"钉在真实数字上：全库 4960 次暂停里，60 秒内又开阀的占 70.8%，
    90 秒内占 87.2%，180 秒内占 98.8%。阈值调大，被打断的人就越少。
    """
    sessions = [row for row in door_link.list_sessions("dev-1") if row["door_no"] == 6]
    segments = [seg for row in sessions for seg in door_link.list_segments_of(row["id"])]
    hold = seconds_between(str(segments[0]["closed_at"]), str(segments[1]["opened_at"]))

    assert hold == 63  # 秒级取整（截断）：16:58:27.568 → 16:59:31.213
    assert hold > 60  # 所以 60 秒的阈值会在他回来之前动手


def test_the_real_incomplete_session_reports_a_mismatch(real_rows: None) -> None:
    """段没记齐的那一场：设备报 900 秒，我们的两段只累到 600——差额就是"有段没进表"。"""
    row = next(item for item in door_link.list_sessions("dev-1") if item["door_no"] == 2)

    assert row["total_flow_s"] < row["device_flow_s"]
    assert row["segments_complete"] is False
    assert row["observed_span"] == "gap"


# ── 判据落在真实形状上 ────────────────────────────────────────────────────


def test_run_started_at_on_the_real_consecutive_run(real_rows: None) -> None:
    """真实的那一串：同一喷头同一学号，间隔 92 秒与 12 秒 → 算同一串，起点是最早那场。

    这一串是手工连点出来的，但形状与我们的续期一样：都是"先释放、几秒后又分配"。
    差别只在间隔量级——我们自己的续期是秒级，真人走了再回来是分钟级；而这里隔了 92 秒，
    仍落在 300 秒那条线以内。
    """
    from app.modules.doors import crud as doors_store

    with session_scope() as session:
        # 同一喷头同一学号：间隔 92 秒仍落在 300 秒那条线以内，起点是最早那场
        assert doors_store.run_started_at(session, "dev-1", 3, "2026000001", 300) == "2026-10-03T18:50:22.210Z"
        # 把那条线收到 60 秒：92 秒那一跳被划断，起点跟着挪到最后两场里最早的那一场
        assert doors_store.run_started_at(session, "dev-1", 3, "2026000001", 60) == "2026-10-03T18:52:18.233Z"
        # 另一个喷头、另一个学号：只有一场，它自己就是起点
        assert doors_store.run_started_at(session, "dev-1", 6, "2026000002", 300) == "2026-10-03T16:53:57.213Z"


def _candidate(door_no: int, cert_no: str, state: DoorState, assigned_at: str, paused_at: str = "") -> Candidate:
    return Candidate(
        session_id=0,
        device_id="dev-1",
        door_no=door_no,
        cert_no=cert_no,
        state=state,
        assigned_at=assigned_at,
        paused_at=paused_at,
        run_started_at=assigned_at,
    )


def test_decide_on_a_real_never_watered_session() -> None:
    """真实形状（门 1）：分配后一直没开阀 → 预约续期。"""
    trigger = decide(
        _candidate(1, "2026000001", DoorState.WAITING, "2026-10-03T21:57:02.488Z"),
        CONFIG,
        "2026-10-03T21:58:02.488Z",
    )

    assert trigger is not None
    assert trigger.job == JOB_RENEW_WAIT
    assert trigger.delay_s == 210.0  # 270 − 已经过去 60 秒


def test_decide_on_a_real_two_segment_session() -> None:
    """真实形状（门 6）：真洗了、正在淋浴中 → 会话续期，从分配时刻起算。"""
    trigger = decide(
        _candidate(6, "2026000002", DoorState.BATHING, "2026-10-03T16:53:57.213Z"),
        CONFIG,
        "2026-10-03T16:58:00.000Z",
    )

    assert trigger is not None
    assert trigger.job == JOB_RENEW_SESSION
    assert trigger.delay_s == 898.0  # 1140 − 242（`seconds_between` 截断）


def test_decide_on_a_real_paused_session() -> None:
    """真实形状：门 6 第二次关阀在 17:02:27.196（会话的 `last_seen_at` 就是那一刻）。"""
    trigger = decide(
        _candidate(6, "2026000002", DoorState.PAUSED, "2026-10-03T16:53:57.213Z", "2026-10-03T17:02:27.196Z"),
        CONFIG,
        "2026-10-03T17:03:30.196Z",
    )

    assert trigger is not None
    assert trigger.job == JOB_AUTO_SETTLE
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
    from app.modules.doors.models import DoorSession

    with session_scope() as session:
        session.add(
            DoorSession(
                device_id=_device_pk(),
                door_no=1,
                cert_no="2026000001",
                assigned_at=_moment("2026-10-03T21:57:02.488Z"),
                assigned_source="inferred",
                observed_span="continuous",
                ended_at=None,
                last_seen_at=_moment("2026-10-03T21:57:02.488Z"),
                total_flow_s=0,
                device_flow_s=0,
                segments_complete=False,
            )
        )
        session.commit()
    door_link.feed(
        "dev-1",
        _frame(
            captured_at="2026-10-03T21:57:03.244Z",
            card_no=0,
            cert_no="2026000001",
            acc_no="7000000001",
            open_time="2026-10-03T21:56:56.000Z",
        ),
    )

    found = _read_snapshots("dev-1")

    assert len(found) == 1
    assert found[0].state is DoorState.WAITING  # 分配了、还没开阀
    assert found[0].cert_no == "2026000001"
    assert found[0].paused_at == ""  # 没暂停过
    assert found[0].run_started_at == found[0].assigned_at  # 只此一场，起点就是它自己


def test_a_real_shape_ends_in_a_settle(
    real_rows: None, client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """拿真实形状跑全链路：一帧进来，到命令出去。

    领域层的两个入口在这里**真跑**（前置检查、守卫都在），只把命令下发换成可控的桩。
    所以设备必须已信任且在线——那是领域层的第一句守卫。
    """
    calls: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        calls.append((action, params))
        return {"status": outcomes.OK.value, "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(scheduler.config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(device_actions, "send_command_and_wait", fake_send)
    door_link.feed(
        "dev-1",
        _frame(
            captured_at="2026-10-03T21:57:03.244Z",
            card_no=0,
            cert_no="2026000001",
            acc_no="7000000001",
            open_time="2026-10-03T20:57:03.000Z",  # 一小时前分配、一直没开阀
        ),
    )

    async def body() -> None:
        scheduler.start()
        scheduler.on_frame("dev-1")
        await _let_the_loop_run()

        assert [action for action, _params in calls] == ["session.settle"]

    with device_link.trusted_online(client, "dev-1", admin_headers):
        asyncio.run(body())
