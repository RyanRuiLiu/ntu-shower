"""推导结果的不变式：跑一串帧，再逐条验。

这些是**执行机制**而不是人工约定。推导规则写在注释里，但"规则没被写错"这件事只有
可判红的断言能守住。旧系统没有这些——它那 100 天的时长算错过、会话也并错过人，直到
重放才发现。

用一串刻意覆盖 R2→R5 各类转换的帧跑完，再对库里的结果逐条验。
"""

from __future__ import annotations

import itertools
from typing import Any

from app.core import db
from app.modules.doors import service


def _door(door_no: int, **overrides: object) -> dict[str, Any]:
    door: dict[str, Any] = {
        "door_no": door_no,
        "card_no": 0,
        "cert_no": "",
        "acc_no": "",
        "bath_time_s": 0,
        "status_raw": 0,
    }
    door.update(overrides)
    return door


def _occupied(door_no: int, who: str, **overrides: object) -> dict[str, Any]:
    """一个被占着的门。`acc_no` 跟着证件号走，好让"人"在两处一致。"""
    return _door(door_no, card_no=1, cert_no=who, acc_no=f"acc-{who}", **overrides)


def _frame(doors: list[dict[str, Any]], at: str) -> dict[str, Any]:
    return {"snapshot_id": f"inv-{at}", "source": service.SOURCE_DB_WATCH, "captured_at": at, "doors": doors}


#: 一串覆盖各类转换的帧。门 2 第一次出现时就已经被占着——那走的是"首帧即占用"，
#: 不记事件但要开会话，恒等式里必须单列这一项。
#:
#: **帧里的 `bath_time_s` 必须等于那条 SQL 会算出的值**（`|inTime − now|` 逐段累加）：
#: 设备就是这么写的，它自己算的累计与两个读数的差必然一致。写成不一致的数（比如两个
#: 时刻差 50 秒却报 60），验的就不是实现而是那个错数。
SEQUENCE = [
    # R2 分配 → R4 开阀 → R4 暂停 → R4 恢复
    (_frame([_door(1)], "2026-09-30T12:00:00.000Z"), None),
    (_frame([_occupied(1, "甲")], "2026-09-30T12:00:10.000Z"), None),
    (_frame([_occupied(1, "甲", in_time="2026-09-30T12:00:20.000Z")], "2026-09-30T12:00:20.000Z"), None),
    (
        _frame([_occupied(1, "甲", bath_time_s=50, close_time="2026-09-30T12:01:10.000Z")], "2026-09-30T12:01:10.000Z"),
        None,
    ),
    (
        _frame([_occupied(1, "甲", in_time="2026-09-30T12:02:00.000Z", bath_time_s=50)], "2026-09-30T12:02:00.000Z"),
        None,
    ),
    # R5 换人：状态矩阵不变，人变了
    (
        _frame([_occupied(1, "乙", in_time="2026-09-30T12:03:00.000Z", bath_time_s=50)], "2026-09-30T12:03:00.000Z"),
        None,
    ),
    # R3 出门
    (_frame([_door(1)], "2026-09-30T12:03:30.000Z"), None),
    # 门 2 首次出现即占用（首帧），随后跨盲区（间隔 3600 秒 > SESSION_MAX_S）
    (_frame([_door(1), _occupied(2, "丙", in_time="2026-09-30T13:00:00.000Z")], "2026-09-30T13:00:00.000Z"), None),
    (_frame([_door(1)], "2026-09-30T14:00:00.000Z"), None),
]


def _run(device_id: str) -> None:
    for frame, _ in SEQUENCE:
        service.process_snapshot(device_id, frame)


def test_invariants_over_a_frame_sequence(fresh_db: str) -> None:
    """七条不变式，逐条验。"""
    device_id = "dev-invariants"
    _run(device_id)
    conn = db.get_conn()

    sessions = conn.execute("SELECT * FROM usage_session WHERE device_id = ? ORDER BY id", (device_id,)).fetchall()
    assert sessions, "这一串帧应当产出会话"

    # 1 · assigned_at ≤ ended_at
    for row in sessions:
        if row["ended_at"] is not None:
            assert row["assigned_at"] <= row["ended_at"], f"起点晚于终点：{dict(row)}"

    # 2 · 每一段都落在它所属会话的 [assigned_at, ended_at] 内
    segments = conn.execute(
        "SELECT g.opened_at, s.assigned_at, s.ended_at, s.door_no FROM usage_segment g"
        " JOIN usage_session s ON s.id = g.session_id"
        " WHERE s.device_id = ? ORDER BY g.id",
        (device_id,),
    ).fetchall()
    assert segments, "这一串帧应当产出段"
    for seg in segments:
        assert seg["assigned_at"] <= seg["opened_at"], f"开阀早于分配：{dict(seg)}"
        if seg["ended_at"] is not None:
            assert seg["opened_at"] <= seg["ended_at"], f"开阀晚于出门：{dict(seg)}"

    # 4 · flow_s ∈ [0, 一天]
    for row in sessions:
        assert 0 <= row["flow_s"] <= service.MAX_BATH_S, f"出水秒数越界：{dict(row)}"

    # 5 · 同一门的会话区间互不重叠
    by_door: dict[int, list[tuple[str, str]]] = {}
    for row in sessions:
        end = row["ended_at"] or row["last_seen_at"]
        by_door.setdefault(row["door_no"], []).append((row["assigned_at"], end))
    for door_no, spans in by_door.items():
        spans.sort()
        for (_, prev_end), (next_start, _) in itertools.pairwise(spans):
            assert prev_end <= next_start, f"门{door_no} 的会话区间重叠：{spans}"

    # 6 · 事件链闭合：同门有序，前一条 to_state == 后一条 from_state
    events = conn.execute(
        "SELECT door_no, from_state, to_state FROM door_state_event WHERE device_id = ? ORDER BY id",
        (device_id,),
    ).fetchall()
    previous: dict[int, str] = {}
    for event in events:
        door_no = event["door_no"]
        if door_no in previous:
            assert previous[door_no] == event["from_state"], f"门{door_no} 的事件链断裂：{dict(event)}"
        previous[door_no] = event["to_state"]

    # 7 · 会话数 == 进入占用态的迁移数 + 首帧即占用的门数 + 换人次数
    occupied = {"waiting", "bathing", "paused"}
    idle = {"free", "selected", "repair", "disabled"}
    entered = sum(1 for e in events if e["from_state"] in idle and e["to_state"] in occupied)
    first_frame_occupied = 1  # 门2 在它第一次出现的那一帧就已经被占着
    handovers = 1  # 甲 → 乙
    assert len(sessions) == entered + first_frame_occupied + handovers, (
        f"会话数对不上：分配 {entered} + 首帧即占用 {first_frame_occupied} + 换人 {handovers}"
    )


def test_cross_gap_does_not_join_two_baths(fresh_db: str) -> None:
    """跨盲区的两帧不被接成一段水。

    设备满 SESSION_MAX_S 会强制释放，所以间隔超过它的"同一段水"不可能存在——无论两帧
    看起来多像连续出水。
    """
    device_id = "dev-inv-gap"
    t0 = "2026-09-30T13:00:00.000Z"
    far = "2026-09-30T14:00:00.000Z"  # 与 t0 相隔 3600 秒 > SESSION_MAX_S

    service.process_snapshot(device_id, _frame([_occupied(2, "丙", in_time=t0)], t0))
    service.process_snapshot(device_id, _frame([_occupied(2, "丙", in_time=t0)], far))

    rows = db.get_conn().execute("SELECT * FROM usage_session WHERE device_id = ? ORDER BY id", (device_id,)).fetchall()
    # 盲区 3600 秒远超一次占用的物理上限，中间必然发生过我们没看见的释放，所以是**两次会话**：
    # 前一场在我们最后看见它的时刻收尾，本帧另起一场。并成一场的话它永远不结束、起点还
    # 落在几小时前，而"跨盲区那一段"本来就没有任何证据。
    assert len(rows) == 2
    assert rows[0]["ended_at"] == t0
    assert rows[0]["last_seen_at"] == t0, "它停在最后一次确认占着的那一帧"
    assert rows[0]["flow_s"] == 0, "跨盲区那一段没有证据，不该计入"
    assert rows[0]["observed_span"] == "gap"
    assert rows[1]["assigned_at"] == far, "新的一场只能从我们看见它的那一刻起算"


def test_occupant_change_splits_the_account(fresh_db: str) -> None:
    """换人当场结旧开新，两个人的时长绝不并进同一条。"""
    device_id = "dev-inv-swap"
    _run(device_id)

    rows = (
        db.get_conn()
        .execute(
            "SELECT cert_no, assigned_at, ended_at, flow_s FROM usage_session"
            " WHERE device_id = ? AND door_no = 1 ORDER BY id",
            (device_id,),
        )
        .fetchall()
    )
    assert [row["cert_no"] for row in rows] == ["甲", "乙"], "门1 应当是两个人的两笔账"
    assert rows[0]["ended_at"] == rows[1]["assigned_at"], "旧账的终点就是新账的起点"
    assert rows[0]["flow_s"] > 0, "甲那一段确实出过水"


def test_flow_s_counts_only_observed_segments(fresh_db: str) -> None:
    """出水秒数只累加有观测证据的连续段。

    恢复那一段（12:02:00 → 12:03:00）上一帧还在出水，就计入；而门2 那次跨了 3600 秒的
    间隔没有任何证据，一秒都不计。
    """
    device_id = "dev-inv-flow"
    _run(device_id)

    rows = (
        db.get_conn()
        .execute("SELECT door_no, cert_no, flow_s FROM usage_session WHERE device_id = ? ORDER BY id", (device_id,))
        .fetchall()
    )
    by_cert = {row["cert_no"]: row["flow_s"] for row in rows}
    assert by_cert["甲"] == 110, "甲那次：第一段 50 秒（12:00:20→12:01:10）+ 第二段 60 秒（12:02:00→12:03:00）"
    assert by_cert["乙"] == 30, "乙那一次：从分配到释放"
    assert by_cert["丙"] == 0, "丙那次跨了盲区，没有证据"
