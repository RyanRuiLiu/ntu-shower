"""推导的数值与边界：每个用例断言具体数字，不是"不报错"。

算法对不对由这些数字说话。覆盖三类最容易出错的地方：

- **优先级**：七态判据自上而下，报修与停用压过占用、占用压过选中；
- **边界**：会话上限那道线取闭区间——恰好 1200 秒仍算连续，1201 秒就断；
- **畸形输入**：能识别的消化、不能识别的丢弃，都不许污染状态。

边界值单独列出来，是因为它们是"差一个等于全错"的地方：`> 1200` 写成 `>= 1200`，
每一次恰好满 20 分钟的会话都会被切成两段，而平时看不出来。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core import db, device_clock
from app.core.clock import shift_rfc3339
from app.modules.doors import repository, service

T0 = "2026-09-30T12:00:00.000Z"
T1 = "2026-09-30T12:01:00.000Z"
T2 = "2026-09-30T12:02:00.000Z"
T3 = "2026-09-30T12:03:00.000Z"
T4 = "2026-09-30T12:04:00.000Z"
T5 = "2026-09-30T12:05:00.000Z"
IN = "2026-09-30T12:00:00.000Z"  # 任意合法时刻，只用来表达"正在出水"
#: 关阀时刻。与 [IN] 相距 100 秒——设备结算本段时写下的 `bath_time_s` 就是这两个读数之差
#: （`bathTime += |inTime − now|`），所以用它造数据时段长才自洽。
CLOSE = "2026-09-30T12:01:40.000Z"


def _door(door_no: int = 1, **overrides: object) -> dict[str, Any]:
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


def _frame(doors: list[dict[str, Any]], at: str) -> dict[str, Any]:
    return {
        "snapshot_id": f"edge-{at}-{len(doors)}",
        "source": service.SOURCE_DB_WATCH,
        "captured_at": at,
        "doors": doors,
    }


def _plus(rfc3339: str, seconds: int) -> str:
    """RFC 3339 加秒。与被测系统走同一个工具，免得两边对时刻的理解分叉。"""
    shifted = shift_rfc3339(rfc3339, seconds)
    assert shifted is not None
    return shifted


def _segments_of(session_id: int) -> list[Any]:
    """某个会话的全部段，按开阀先后。"""
    return (
        db.get_conn()
        .execute("SELECT * FROM usage_segment WHERE session_id = ? ORDER BY opened_at, id", (session_id,))
        .fetchall()
    )


def _sessions(device_id: str) -> list[Any]:
    return db.get_conn().execute("SELECT * FROM usage_session WHERE device_id = ? ORDER BY id", (device_id,)).fetchall()


# ════════════════════════════════════════════════════════════════════════
# 一、状态派生：七态与优先级
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("door", "expected"),
    [
        (_door(), "free"),
        (_door(acc_no="A001"), "waiting"),
        (_door(card_no=1001, cert_no="C"), "waiting"),
        (_door(acc_no="A001", in_time=IN), "bathing"),
        (_door(acc_no="A001", bath_time_s=300, close_time=CLOSE), "paused"),
        (_door(status_raw=2), "selected"),
        (_door(status_raw=1), "repair"),
        (_door(status_raw=3), "disabled"),
        # 优先级：报修与停用压过占用，占用压过选中
        (_door(status_raw=1, acc_no="A001", in_time=IN), "repair"),
        (_door(status_raw=3, acc_no="A001", bath_time_s=300, close_time=CLOSE), "disabled"),
        (_door(status_raw=2, acc_no="A001"), "waiting"),
        # paused 要"累计过出水"与"关过阀"两条同时成立
        (_door(acc_no="A001", bath_time_s=300), "waiting"),
        (_door(acc_no="A001", close_time=CLOSE), "waiting"),
    ],
)
def test_state_derivation(fresh_db: str, door: dict[str, Any], expected: str) -> None:
    """七态的判据与优先级，逐条验。"""
    device_id = "dev-state"
    service.process_snapshot(device_id, _frame([door], T0))
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == expected


# ════════════════════════════════════════════════════════════════════════
# 二、会话：四个时刻与淋浴时长
# ════════════════════════════════════════════════════════════════════════


def test_full_session_four_moments(fresh_db: str) -> None:
    """一次完整使用：分配 → 开阀 → 暂停 → 释放。

    起点与终点在会话表上，开阀时刻与那一段的长度在段表上——两者粒度不同，所以不再挤在
    同一行。
    """
    device_id = "dev-full-session"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], "2026-09-30T12:00:00.000Z"))
    service.process_snapshot(
        device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], "2026-09-30T12:00:30.000Z")
    )
    service.process_snapshot(
        device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], "2026-09-30T12:01:30.000Z")
    )
    # 设备在暂停那一刻结算本段：12:00:30 → 12:01:30 正好 100 秒
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=100, close_time=CLOSE)], "2026-09-30T12:02:10.000Z"),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:02:30.000Z"))

    sessions = _sessions(device_id)
    assert len(sessions) == 1
    row = sessions[0]
    assert row["assigned_at"] == "2026-09-30T12:00:00.000Z"
    assert row["last_seen_at"] == "2026-09-30T12:02:10.000Z", "停在最后一次确认占用，不是结束帧"
    assert row["ended_at"] == "2026-09-30T12:02:30.000Z"
    assert row["cert_no"] == "C"
    assert row["flow_s"] == 100

    segments = _segments_of(row["id"])
    assert len(segments) == 1
    assert segments[0]["opened_at"] == "2026-09-30T12:00:30.000Z"
    assert segments[0]["flow_s"] == 100
    assert segments[0]["source"] == "measured"


def test_leaving_while_bathing_keeps_last_segment(fresh_db: str) -> None:
    """开着阀直接出门：那一帧的 inTime 与 bathTime 都被清零，证据只在上一帧。

    这是设备"整体清零"写法的直接后果——只看本帧会把每次不关阀就结算都记成 0 秒。
    """
    device_id = "dev-leave-bathing"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:03:20.000Z"))

    row = _sessions(device_id)[0]
    assert row["flow_s"] == 200
    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == T0
    assert segments[0]["source"] == "observed", "没有 close_time，只能用观测差"


def test_assign_then_settle_without_water(fresh_db: str) -> None:
    """分配后没开阀就结算：会话存在，但从未观测到出水——一段都没有。"""
    device_id = "dev-no-water"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], T0))
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:00:20.000Z"))

    row = _sessions(device_id)[0]
    assert _segments_of(row["id"]) == []
    assert row["flow_s"] == 0
    assert row["ended_at"] == "2026-09-30T12:00:20.000Z"


def test_repair_interrupts_session(fresh_db: str) -> None:
    """淋浴中被打上维修：占用态结束，会话收尾。"""
    device_id = "dev-repair"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door(status_raw=1)], "2026-09-30T12:00:40.000Z"))

    row = _sessions(device_id)[0]
    assert row["ended_at"] == "2026-09-30T12:00:40.000Z"
    assert repository.get_all_live(device_id)[1]["state"] == "repair"


def test_two_sessions_are_separate(fresh_db: str) -> None:
    """两次独立使用各记一条，时长不互相污染。"""
    device_id = "dev-two-sessions"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door()], T1))
    service.process_snapshot(device_id, _frame([_door(acc_no="B", cert_no="D", in_time=IN)], T2))
    service.process_snapshot(device_id, _frame([_door()], T3))

    rows = _sessions(device_id)
    assert [row["cert_no"] for row in rows] == ["C", "D"]
    assert [row["flow_s"] for row in rows] == [60, 60]


def test_handover_leaves_the_previous_accumulation_unowned(fresh_db: str) -> None:
    """前一个人停在暂停态、后人分配覆盖（实测 10 次换人都是这一形态）。

    前人那一场我们只看见**一帧**（暂停态、没有 `in_time`），凭它推不出段——设备累计的
    200 秒没有起止。所以那 200 秒**没有归属**：它只体现在 `device_flow_s` 里。硬把它塞进
    某一段，等于替前人编出那一段的起止。
    """
    device_id = "dev-handover-paused"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", bath_time_s=200, close_time=CLOSE)], T0))
    # 设备不清零累计值：它是"这台设备出过的水"，换人不重置（后人的帧里仍是 200）
    service.process_snapshot(device_id, _frame([_door(acc_no="B", cert_no="D", bath_time_s=200)], T1))

    first, second = _sessions(device_id)
    assert first["cert_no"] == "C"
    assert first["flow_s"] == 0, "只看见一帧暂停态，造不出段"
    assert second["cert_no"] == "D"
    assert second["flow_s"] == 0, "后人还没出过水"
    assert second["device_flow_s"] == 200, "设备累计里留着前人那一场，但它没有归属"
    assert second["segments_complete"] == 0, "两边对不上——这正是它要表达的事"


def test_same_occupant_repeated_frames_do_not_split(fresh_db: str) -> None:
    """同一个人连报五帧：不开新会话、不记事件。"""
    device_id = "dev-repeat"
    for index in range(5):
        at = f"2026-09-30T12:00:{index * 10:02d}.000Z"
        service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], at))

    assert len(_sessions(device_id)) == 1
    events = (
        db.get_conn().execute("SELECT COUNT(*) FROM door_state_event WHERE device_id = ?", (device_id,)).fetchone()[0]
    )
    assert events == 0


# ════════════════════════════════════════════════════════════════════════
# 三、边界：会话上限那道线
# ════════════════════════════════════════════════════════════════════════


def test_gap_exactly_at_session_limit_is_still_continuous(fresh_db: str) -> None:
    """间隔恰好等于会话上限（1200 秒）时仍算连续——判据取闭区间。

    设备要满 1200 秒才会强制关阀，所以"这一帧到下一帧之间它一直在出水"在间隔恰好
    等于 1200 秒时仍有可能。
    """
    device_id = "dev-edge-exact"
    limit = service.SESSION_MAX_S
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, limit)))

    assert _sessions(device_id)[0]["flow_s"] == limit


def test_gap_one_second_over_the_limit_breaks_the_segment(fresh_db: str) -> None:
    """超出一秒即判断开：那一段没有被观测到，一秒都不算。"""
    device_id = "dev-edge-over"
    limit = service.SESSION_MAX_S
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, limit + 1)))

    assert _sessions(device_id)[0]["flow_s"] == 0


def test_offline_gap_does_not_inflate_flow(fresh_db: str) -> None:
    """离线 8 小时后仍在出水：那 8 小时不可知，一秒都不计。

    盲区远超一次占用的物理上限，所以前后是**两次会话**：前一场在我们最后看见它的时刻收尾，
    本帧另起一场。并成一场的话它永远不结束、`flow_s` 还会把 8 小时算进去——旧实现用
    "当前时刻减 in_time"，实测出现过 24 小时的脏值，而且只增不减。
    """
    device_id = "dev-offline-gap"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, 100)))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, 8 * 3600)))

    rows = _sessions(device_id)
    assert len(rows) == 2
    assert rows[0]["flow_s"] == 100, "盲区那段不计，只剩我们看见的那 100 秒"
    assert rows[0]["ended_at"] == _plus(T0, 100), "在我们最后看见它的时刻收尾"
    assert rows[0]["observed_span"] == "gap"
    assert rows[1]["assigned_at"] == _plus(T0, 8 * 3600), "新的一场只能从我们看见它的那一刻起算"


def test_session_crossing_gap_keeps_last_seen_as_the_bound(fresh_db: str) -> None:
    """跨夜：次日上午才再看见这个门，中间十几个小时是空的。

    `last_seen_at` 是可信用作结束下界的那一个，`ended_at` 只是"下次见到它的时刻"——盲区
    超过一次占用的上限时，两者不再是同一件事。
    """
    device_id = "dev-cross-night"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], _plus(T0, 3600)))
    service.process_snapshot(device_id, _frame([_door()], _plus(T0, 12 * 3600)))

    rows = _sessions(device_id)
    assert len(rows) == 2, "盲区 11 小时，前后是两次会话"
    assert rows[0]["last_seen_at"] == T0
    assert rows[0]["ended_at"] == T0, "两帧隔了一小时，第一场在最初那一帧就收尾了"
    assert rows[0]["observed_span"] == "gap"
    assert rows[1]["assigned_at"] == _plus(T0, 3600)


# ════════════════════════════════════════════════════════════════════════
# 四、畸形输入与幂等
# ════════════════════════════════════════════════════════════════════════


def test_non_object_entries_are_skipped(fresh_db: str) -> None:
    """帧里的非对象条目直接跳过，不影响合法的那一条。"""
    device_id = "dev-non-object"
    payload = _frame([_door(acc_no="A")], T0)
    payload["doors"] = [None, 1, "x", [], payload["doors"][0]]
    service.process_snapshot(device_id, payload)

    assert repository.get_all_live(device_id)[1]["state"] == "waiting"


def test_garbage_time_is_not_treated_as_present(fresh_db: str) -> None:
    """解析不了的时间字段不算"有"——否则一条乱码会被读成"正在出水"。"""
    device_id = "dev-garbage-time"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", in_time="不是时间", close_time=1782135693)], T0))

    assert repository.get_all_live(device_id)[1]["state"] == "waiting"


def test_first_frame_does_not_fabricate_a_transition(fresh_db: str) -> None:
    """首帧没有"从哪来"，不该记一条空前态的迁移。"""
    device_id = "dev-first-frame"
    service.process_snapshot(device_id, _frame([_door(acc_no="A")], T0))

    events = (
        db.get_conn().execute("SELECT COUNT(*) FROM door_state_event WHERE device_id = ?", (device_id,)).fetchone()[0]
    )
    assert events == 0
    assert len(_sessions(device_id)) == 1, "但占用态要开会话"


def test_replaying_the_same_frame_is_idempotent(fresh_db: str) -> None:
    """同一帧重复处理不产生副作用：不开新会话、不记事件、时长不翻倍。"""
    device_id = "dev-idempotent"
    frame = _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0)
    for _ in range(3):
        service.process_snapshot(device_id, frame)

    assert len(_sessions(device_id)) == 1
    assert _sessions(device_id)[0]["flow_s"] == 0
    events = (
        db.get_conn().execute("SELECT COUNT(*) FROM door_state_event WHERE device_id = ?", (device_id,)).fetchone()[0]
    )
    assert events == 0


def test_missing_door_in_a_later_frame_keeps_its_state(fresh_db: str) -> None:
    """设备漏报某个门时，那个门不该被判成空闲。"""
    device_id = "dev-missing-door"
    service.process_snapshot(device_id, _frame([_door(1, acc_no="A"), _door(2)], T0))
    service.process_snapshot(device_id, _frame([_door(2)], T1))

    live = repository.get_all_live(device_id)
    assert live[1]["state"] == "waiting", "缺席的门保留原状态"
    assert repository.get_open_session(device_id, 1) is not None
    assert len(_sessions(device_id)) == 1, "会话没有因为漏报而结束"


# ════════════════════════════════════════════════════════════════════════
# 五、接手时那边还被占着
# ════════════════════════════════════════════════════════════════════════


def test_first_frame_already_occupied_marks_the_observation_bound(fresh_db: str) -> None:
    """接手时那边还被占着：起点只能记本帧，它是**观测下界**不是事实起点。

    服务端重启、agent 重装、设备断网重连之后，本端拿到的第一帧就可能落在用户洗浴过程中。

    **这里推不出真实起点**，试过一版倒推是错的：`bath_time_s` 累加的是已经结算完的各段
    （设备要到暂停那一刻才结算），正在进行的这一段还没进去。所以"本帧减 bath_time_s"
    给出的是上一次暂停前的位置，不是首次出水。而"本段已经多久"无从得知——设备没有
    "当前时刻"这类字段可作参考点。

    但那段水是真实的，**它的秒数已经在 `bath_time_s` 里**，会被计入 `flow_s`。
    """
    device_id = "dev-takeover"
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=180, in_time=IN)], T0),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == T0, "观测下界：我们确知它此刻占着，仅此而已"
    assert row["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED, "这一帧没带 open_time，平移不了"
    assert row["flow_s"] == 0, "刚开阀，本段还没有长度"
    assert row["device_flow_s"] == 180, "设备说它出过 180 秒水，但那 180 秒我们看不见起止"

    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == T0, "无偏差可平移，段起点退回首次观测到出水的那一帧"
    assert segments[0]["closed_at"] is None, "还没关阀，长度等它关阀那一刻才定"
    assert segments[0]["source"] == "", "在途段的来源还没有定论"


def test_takeover_with_open_time_infers_the_real_start(fresh_db: str) -> None:
    """设备带着 `open_time` 时，起点由它平移而来——那是**真实的分配时刻**，不是下界。

    `open_time` 是分配那一刻设备写下的，而它在整场会话里不变：暂停与恢复都不改它，
    只有释放才整体清零。所以无论中间暂停过几次，平移回来都还是那个起点。

    平移的依据是设备回的 pong 算出的钟偏差（见 core/device_clock）。
    """
    device_id = "dev-takeover-inferred"
    # 设备钟比服务端慢 30 秒：偏差（服务端 − 设备）为正
    device_clock.load(device_id, 30.0)
    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    acc_no="A",
                    cert_no="C",
                    bath_time_s=600,
                    open_time="2026-09-30T11:57:30.000Z",  # 用户分配时刻（设备钟）
                    in_time="2026-09-30T11:58:00.000Z",  # 本段开始出水（设备钟）
                )
            ],
            T0,
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_source"] == service.SESSION_ASSIGNED_INFERRED
    assert row["assigned_at"] == "2026-09-30T11:58:00.000Z", "设备说的 11:57:30，加 30 秒"
    assert row["flow_s"] == 90, "本段：设备说的 11:58:30 到我们看见它的 12:00:00"
    assert row["device_flow_s"] == 600, "设备那 600 秒是之前几段结算的，我们不知道它们的起止"

    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == "2026-09-30T11:58:30.000Z", "开阀时刻由 in_time 平移而来"


def test_inferred_start_makes_the_span_cover_the_duration(fresh_db: str) -> None:
    """起点平移过来之后，跨度就盖得住时长了——不再出现"时长大于跨度"。

    那是"起点只是下界"时的必然现象（见 service 的 _maintain_session）：下界比真实起点
    晚，而 `flow_s` 里含着更早那段已结算的出水。平移把起点推回真实位置，现象随之消失。

    这组数据贴着设备上限造：总出水 1170 秒、跨度也是 1170 秒，都恰好落在 1200 以内。
    """
    device_id = "dev-takeover-span-fixed"
    device_clock.load(device_id, 30.0)
    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    acc_no="A",
                    cert_no="C",
                    bath_time_s=300,  # 之前几段已结算的出水
                    open_time="2026-09-30T11:45:00.000Z",  # 分配（设备钟）
                    in_time="2026-09-30T11:50:00.000Z",  # 本段开始出水（设备钟）
                )
            ],
            T0,
        ),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:05:00.000Z"))

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T11:45:30.000Z", "分配平移"
    assert row["flow_s"] == 870, "本段：设备说的 11:50:30 到关阀那一刻"
    span = 1170  # 11:45:30 → 12:05:00
    assert row["flow_s"] <= span, "时长落在跨度之内——起点是事实，不再是下界"


def test_takeover_session_duration_stays_within_its_span(fresh_db: str) -> None:
    """接手那一次（这一帧没带 `open_time`）时长仍落在跨度之内。

    起点只能落到本帧，它是**观测下界**；而时长只累加我们看见的那一段——两边都从"我们看见
    它的那一刻"起算，所以 `flow_s ≤ ended_at − assigned_at` 恒成立。

    设备累计的那 600 秒不在里面：我们不知道它的起止，只在 `device_flow_s` 里留痕。旧实现
    把那 600 秒并进 `flow_s`，于是时长大于跨度；现在两者各归各位。
    """
    device_id = "dev-takeover-span"
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=600, in_time=IN)], T0),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:05:00.000Z"))

    row = _sessions(device_id)[0]
    span = 300  # 12:00:00 → 12:05:00，我们只看了这五分钟
    assert row["flow_s"] == 300, "我们看见它出水的这五分钟"
    assert row["flow_s"] <= span
    assert row["device_flow_s"] == 600, "设备说 600，多出来的那 300 秒没有起止"
    assert row["segments_complete"] == 0


# ════════════════════════════════════════════════════════════════════════
# 六、开阀时刻：三条来源与它们的边界
# ════════════════════════════════════════════════════════════════════════


def test_takeover_while_paused_recovers_the_open_time(fresh_db: str) -> None:
    """接手时人已经洗到一半（暂停态）：从关阀时刻与累计秒数里把开阀时刻拿回来。

    这是冷启动时唯一还能拿回它的途径——出水那一帧我们没看见，而 `in_time` 已经被设备
    清空（见 service 的 [_derive_segment]）。两个量都来自同一口钟，绝对值不可信，但"关阀
    时刻"经偏差平移到服务端轴之后，"累计秒数"是纯时长，两者相减就是本段起点。
    """
    device_id = "dev-takeover-paused"
    device_clock.load(device_id, 60.0)  # 设备钟比服务端慢 60 秒

    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    acc_no="A",
                    cert_no="C",
                    bath_time_s=120,
                    open_time="2026-09-30T11:58:00.000Z",  # 分配（设备钟）
                    close_time="2026-09-30T12:01:00.000Z",  # 关阀（设备钟）
                )
            ],
            "2026-09-30T12:10:00.000Z",
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T11:59:00.000Z", "分配平移"
    assert row["flow_s"] == 120

    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == "2026-09-30T12:00:00.000Z", "关阀 12:02 往前数 120 秒"
    assert segments[0]["source"] == "derived", "这一段是我们从设备痕迹里推回来的"
    assert row["segments_complete"] == 0, "推出来的长度就是设备那个值，等式必然成立"


def test_opened_at_comes_from_the_shifted_in_time(fresh_db: str) -> None:
    """正在出水时，开阀时刻取 `in_time` 的平移值，不是我们看见它的那一帧。

    两者差着"设备检测加上报"的那一段。它同时就是这一段的起点——同一份证据喂给两个
    字段，不再各算各的。
    """
    device_id = "dev-open-shift"
    device_clock.load(device_id, 60.0)

    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    acc_no="A",
                    cert_no="C",
                    open_time="2026-09-30T11:58:00.000Z",
                    in_time="2026-09-30T12:00:00.000Z",  # 开阀（设备钟）
                )
            ],
            "2026-09-30T12:10:00.000Z",
        ),
    )

    row = _sessions(device_id)[0]
    assert row["flow_s"] == 540
    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == "2026-09-30T12:01:00.000Z", "设备 12:00 平移到服务端 12:01"
    assert segments[0]["opened_at"] != "2026-09-30T12:10:00.000Z", "不是我们看见它的那一帧"


def test_out_of_range_open_time_is_not_adopted(fresh_db: str) -> None:
    """算出来的开阀时刻落在会话起点之前就不采用——多段之和会把它推得过早。

    判据是 `[assigned_at, 本帧]`：开阀不可能早于分配，也不可能晚于此刻。拿不准就留空，
    宁可没有。
    """
    device_id = "dev-open-out-of-range"
    device_clock.load(device_id, 60.0)

    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    acc_no="A",
                    cert_no="C",
                    bath_time_s=3600,  # 两段之和，远超本段
                    open_time="2026-09-30T11:58:00.000Z",
                    close_time="2026-09-30T12:01:00.000Z",
                )
            ],
            "2026-09-30T12:10:00.000Z",
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T11:59:00.000Z"
    assert _segments_of(row["id"]) == [], "12:02 往前数 3600 秒得到 11:02，早于分配，丢掉"
    assert row["flow_s"] == 0
    assert row["device_flow_s"] == 3600, "设备的话留着，但它凑不出一段"


def test_takeover_does_not_inherit_the_previous_open_time(fresh_db: str) -> None:
    """换人时，新会话的段起点不得沿用前人的。

    新人此刻才分配，他名下的最早值只能是这一刻。前人那一段水在换人那一刻就收尾了——
    它归旧账，不归新账。
    """
    device_id = "dev-takeover-opened"
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="甲", open_time="2026-09-30T11:50:00.000Z", in_time=IN)], T0),
    )
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="B", cert_no="乙", open_time="2026-09-30T12:02:00.000Z", in_time=IN)], T2),
    )

    first, second = _sessions(device_id)
    assert first["cert_no"] == "甲"
    assert second["cert_no"] == "乙"
    assert second["assigned_at"] == "2026-09-30T12:02:00.000Z"
    assert first["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED, "这一帧没带偏差，起点是观测下界"
    assert first["ended_at"] == "2026-09-30T12:02:00.000Z", "旧账在换人那一刻结清，结束时刻是准的"

    segments = _segments_of(second["id"])
    assert segments[0]["opened_at"] == "2026-09-30T12:02:00.000Z", "新人名下的段起点不得早于他分配"


def test_start_later_than_the_frame_is_not_adopted(fresh_db: str) -> None:
    """`open_time` 平移后晚于本帧就不采用——分配不可能发生在我们看见它之后。

    设备钟被往回拨、而偏差还没跟上时会读到这种值。
    """
    device_id = "dev-assign-future"
    device_clock.load(device_id, 3600.0)  # 设备钟慢 1 小时：平移会把读数推后

    service.process_snapshot(
        device_id,
        _frame([_door(cert_no="C", acc_no="A", open_time="2026-09-30T11:10:00.000Z")], "2026-09-30T12:05:00.000Z"),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T12:05:00.000Z", "退回本帧，而不是算出 12:10"
    assert row["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED


def test_start_before_the_last_sighting_is_not_adopted(fresh_db: str) -> None:
    """起点早于**上次看见这个门**的时刻就不采用——更早的会话我们已经见过了。

    上一次看见它还是空闲，分配只可能发生在那之后。2026-10-02 的断连场景就落在这一条上：
    分配写在设备钟被改之前，重连时偏差已经换代。
    """
    device_id = "dev-assign-early"
    device_clock.load(device_id, 0.0)

    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:00:00.000Z"))  # 12:00 它空着
    service.process_snapshot(
        device_id,
        _frame([_door(cert_no="C", acc_no="A", open_time="2026-09-30T11:50:00.000Z")], "2026-09-30T12:10:00.000Z"),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T12:10:00.000Z", "退回本帧，而不是早于上次见它的 11:50"
    assert row["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED


def test_start_far_from_in_time_is_not_adopted(fresh_db: str) -> None:
    """与同帧 `in_time` 的平移值相差超过一次占用的上限，就不采用 `open_time`。

    两个读数出自同一口钟，而一次占用压在 20 分钟内（设备满 `maxbatchtime` 强制释放）。
    差得更多只有一个解释：其中一个是用**另一口钟**写的——设备钟被改过而我们没跟上。

    这是"拨钟时我们离线"**唯一**能识破的一条：那时没有上一帧可参照（判据 2 无从谈起）。
    下例还原 2026-10-02 实测的那一次：断连期间分配（旧钟，12:01）→ 拨快一小时 →
    重连后开阀（新钟，13:05）。`open_time` 平移出来是 11:01，比开阀早了 64 分钟。
    """
    device_id = "dev-assign-skew"
    device_clock.load(device_id, -3600.0)  # 拨钟后：设备钟快 1 小时

    service.process_snapshot(
        device_id,
        _frame(
            [
                _door(
                    cert_no="C",
                    acc_no="A",
                    open_time="2026-09-30T12:01:00.000Z",  # 旧钟写的
                    in_time="2026-09-30T13:05:00.000Z",  # 新钟写的
                )
            ],
            "2026-09-30T12:05:00.000Z",
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T12:05:00.000Z", "退回本帧，而不是算出 11:01"
    assert row["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED

    segments = _segments_of(row["id"])
    assert segments[0]["opened_at"] == "2026-09-30T12:05:00.000Z", "in_time 是新钟写的，平移回来是对的"
