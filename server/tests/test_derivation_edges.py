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
CLOSE = "2026-09-30T12:30:00.000Z"


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
# 二、会话：四个时刻与出水时长
# ════════════════════════════════════════════════════════════════════════


def test_full_session_four_moments(fresh_db: str) -> None:
    """一次完整使用：刷卡 → 开阀 → 暂停 → 出门，四个时刻各落在正确的帧上。"""
    device_id = "dev-full-session"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], "2026-09-30T12:00:00.000Z"))
    service.process_snapshot(
        device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], "2026-09-30T12:00:30.000Z")
    )
    service.process_snapshot(
        device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], "2026-09-30T12:01:30.000Z")
    )
    # 设备在暂停那一刻结算本段：30 → 130 正好 100 秒
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=100, close_time=CLOSE)], "2026-09-30T12:02:10.000Z"),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:02:30.000Z"))

    sessions = _sessions(device_id)
    assert len(sessions) == 1
    row = sessions[0]
    assert row["assigned_at"] == "2026-09-30T12:00:00.000Z"
    assert row["opened_at"] == "2026-09-30T12:00:30.000Z"
    assert row["last_seen_at"] == "2026-09-30T12:02:10.000Z", "停在最后一次确认占用，不是结束帧"
    assert row["ended_at"] == "2026-09-30T12:02:30.000Z"
    assert row["flow_s"] == 100
    assert row["cert_no"] == "C"


def test_leaving_while_bathing_keeps_last_segment(fresh_db: str) -> None:
    """开着阀直接出门：那一帧的 inTime 与 bathTime 都被清零，证据只在上一帧。

    这是设备"整体清零"写法的直接后果——只看本帧会把每次不关阀就结算都记成 0 秒。
    """
    device_id = "dev-leave-bathing"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:03:20.000Z"))

    row = _sessions(device_id)[0]
    assert row["flow_s"] == 200
    assert row["opened_at"] == T0


def test_device_reported_bath_time_wins_when_larger(fresh_db: str) -> None:
    """设备累计值更大且跨度容得下时以它为准——我们的观测可能漏帧。"""
    device_id = "dev-device-wins"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=500, close_time=CLOSE)], "2026-09-30T12:10:00.000Z"),
    )

    row = _sessions(device_id)[0]
    assert row["flow_s"] == 500


def test_assign_then_settle_without_water(fresh_db: str) -> None:
    """入门后没开水就结算：会话存在，但从未观测到出水。"""
    device_id = "dev-no-water"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], T0))
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:00:20.000Z"))

    row = _sessions(device_id)[0]
    assert row["opened_at"] is None
    assert row["flow_s"] == 0
    assert row["ended_at"] == "2026-09-30T12:00:20.000Z"


def test_repair_interrupts_session(fresh_db: str) -> None:
    """使用中被打上报修：占用态结束，会话收尾。"""
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


def test_handover_from_paused_keeps_the_accumulated_water(fresh_db: str) -> None:
    """前一个人暂停中、后一个人刷卡直接覆盖了他的字段（实测 10 次是这一形态）。

    设备累计的出水归**前一个人**——那是他用的水。
    """
    device_id = "dev-handover-paused"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", bath_time_s=200, close_time=CLOSE)], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="B", cert_no="D")], T1))

    rows = _sessions(device_id)
    assert len(rows) == 2
    assert rows[0]["cert_no"] == "C"
    assert rows[0]["flow_s"] == 200, "前一个人的累计没有丢"
    assert rows[1]["cert_no"] == "D"
    assert rows[1]["flow_s"] == 0, "新账从零起算"


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

    旧实现用"当前时刻减 in_time"，实测出现过 24 小时的脏值，而且只增不减，一旦落库
    就永久污染。
    """
    device_id = "dev-offline-gap"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, 100)))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C", in_time=IN)], _plus(T0, 8 * 3600)))

    row = _sessions(device_id)[0]
    assert row["flow_s"] == 100, "盲区那段不计"
    assert row["last_seen_at"] == _plus(T0, 8 * 3600), "但这一帧确实证明它还占着"


def test_session_crossing_gap_keeps_last_seen_as_the_bound(fresh_db: str) -> None:
    """跨夜会话：设备关机前自动结算，我们次日才看到 free。

    `last_seen_at` 是可信用作结束下界的那一个，`ended_at` 只是"下次见到它的时刻"。
    """
    device_id = "dev-cross-night"
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], T0))
    service.process_snapshot(device_id, _frame([_door(acc_no="A", cert_no="C")], _plus(T0, 3600)))
    service.process_snapshot(device_id, _frame([_door()], _plus(T0, 12 * 3600)))

    row = _sessions(device_id)[0]
    assert row["last_seen_at"] == _plus(T0, 3600)
    assert row["ended_at"] == _plus(T0, 12 * 3600)


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
# 五、接手时那边已经在洗
# ════════════════════════════════════════════════════════════════════════


def test_first_frame_already_occupied_marks_the_observation_bound(fresh_db: str) -> None:
    """接手时那边已经在洗：起点只能记本帧，它是**观测下界**不是事实起点。

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
    assert row["opened_at"] == T0, "首次观测到出水就是这一帧"
    assert row["flow_s"] == 180, "但已经出的水一秒不少"


def test_takeover_with_open_time_infers_the_real_start(fresh_db: str) -> None:
    """设备带着 `open_time` 时，起点由它平移而来——那是**真实的刷卡时刻**，不是下界。

    `open_time` 是刷卡那一刻设备写下的，而它在整场会话里不变：暂停与恢复都不改它，
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
                    open_time="2026-09-30T11:57:30.000Z",  # 用户刷卡时刻（设备钟）
                    in_time="2026-09-30T11:58:00.000Z",  # 本段开始出水（设备钟）
                )
            ],
            T0,
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_source"] == service.SESSION_ASSIGNED_INFERRED
    assert row["assigned_at"] == "2026-09-30T11:58:00.000Z", "设备说的 11:57:30，加 30 秒"
    assert row["flow_s"] == 690, "设备累计 600，加上本段从 11:58:30 到 12:00:00 那 90 秒"


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
                    open_time="2026-09-30T11:45:00.000Z",  # 刷卡（设备钟）
                    in_time="2026-09-30T11:50:00.000Z",  # 本段开始出水（设备钟）
                )
            ],
            T0,
        ),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:05:00.000Z"))

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T11:45:30.000Z"
    assert row["flow_s"] == 1170, "设备累计 300 + 本段 570 + 最后那 300"
    span = 1170  # 11:45:30 → 12:05:00
    assert row["flow_s"] <= span, "时长落在跨度之内——起点是事实，不再是下界"


def test_takeover_session_has_duration_greater_than_span(fresh_db: str) -> None:
    """接手那次的时长会**大于**跨度，而且这是对的。

    起点只能落到本帧（这一帧没带 `open_time`，无从平移），而 `flow_s` 里含着我们接手
    之前那段已结算的出水——两者于是不再满足 `flow_s ≤ ended_at − assigned_at`。实测
    100 天里有 5 条如此，其中 3 条正是"数据起点即占用"。

    **不该去"修"它**：拿设备那口钟的差值硬凑会把真实的出水抹掉。要做的只是认出来——
    判据就是这条不等式，据此把这几条单列：时长类指标用它们的 `flow_s`（那个是对的），
    跨度类指标则不能。

    设备带 `open_time` 时起点能平移（见 `_assigned_at`），那时起点就是事实而非下界，
    这类情形自然消失——本用例走的是不带 `open_time` 的那条退路。
    """
    device_id = "dev-takeover-span"
    service.process_snapshot(
        device_id,
        _frame([_door(acc_no="A", cert_no="C", bath_time_s=600, in_time=IN)], T0),
    )
    service.process_snapshot(device_id, _frame([_door()], "2026-09-30T12:05:00.000Z"))

    row = _sessions(device_id)[0]
    span = 300  # 12:00:00 → 12:05:00，我们只看了这五分钟
    assert row["flow_s"] == 900, "设备累计的 600，加上上一帧到现在那 300 秒"
    assert row["flow_s"] > span, "所以时长大于跨度——起点只是下界"


# ════════════════════════════════════════════════════════════════════════
# 六、开阀时刻：三条来源与它们的边界
# ════════════════════════════════════════════════════════════════════════


def test_takeover_while_paused_recovers_the_open_time(fresh_db: str) -> None:
    """接手时人已经洗到一半（暂停态）：从关阀时刻与累计秒数里把开阀时刻拿回来。

    这是冷启动时唯一还能拿回它的途径——出水那一帧我们没看见，而 `in_time` 已经被设备
    清空（见 service 的 [_opened_at]）。两个量都来自同一口钟，绝对值不可信，但"关阀
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
                    open_time="2026-09-30T11:58:00.000Z",  # 刷卡（设备钟）
                    close_time="2026-09-30T12:01:00.000Z",  # 关阀（设备钟）
                )
            ],
            "2026-09-30T12:10:00.000Z",
        ),
    )

    row = _sessions(device_id)[0]
    assert row["assigned_at"] == "2026-09-30T11:59:00.000Z", "刷卡平移"
    assert row["opened_at"] == "2026-09-30T12:00:00.000Z", "关阀 12:02 减 120 秒"
    assert row["flow_s"] == 120


def test_opened_at_comes_from_the_shifted_in_time(fresh_db: str) -> None:
    """正在出水时，开阀时刻取 `in_time` 的平移值，不是我们看见它的那一帧。

    两者差着"设备检测加上报"的那一段。它同时就是出水时长的段起点——同一份证据喂给两个
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
    assert row["opened_at"] == "2026-09-30T12:01:00.000Z", "设备 12:00 平移到服务端 12:01"
    assert row["opened_at"] != "2026-09-30T12:10:00.000Z", "不是我们看见它的那一帧"
    assert row["flow_s"] == 540


def test_out_of_range_open_time_is_not_adopted(fresh_db: str) -> None:
    """算出来的开阀时刻落在会话起点之前就不采用——多段之和会把它推得过早。

    判据是 `[assigned_at, 本帧]`：开阀不可能早于进门，也不可能晚于此刻。拿不准就留空，
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
    assert row["opened_at"] is None, "12:02 减 3600 秒得到 11:02，早于进门"
    assert row["flow_s"] == 3600


def test_takeover_does_not_inherit_the_previous_open_time(fresh_db: str) -> None:
    """换人时，新会话的开阀时刻不得沿用前人的段起点。

    段起点仍要延续给 `flow_s`（那段水从上一帧就开始了），但开阀时刻是"这个人"的：
    新人此刻才进门，他名下的最早值只能是这一刻。两者在这一处必须分开。
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
    assert second["opened_at"] == "2026-09-30T12:02:00.000Z", "新人名下的开阀时刻不得早于他进门"
    assert first["assigned_source"] == service.SESSION_ASSIGNED_OBSERVED, "这一帧没带偏差，起点是观测下界"
    assert first["ended_at"] == "2026-09-30T12:02:00.000Z", "旧账在换人那一刻结清，结束时刻是准的"
