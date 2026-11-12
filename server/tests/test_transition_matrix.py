"""58 格逐格验证：任何一对相邻观测都落进 R1–R5 之一。

规则的表述在 `service.py` 的模块注释里（一张表）。这里把它**逐格跑一遍**——不是抽查
几条路径，而是把五条规则覆盖的每一格都构造出来，断言它的会话动作符合定稿。

格子的构成：状态对 7×7 = 49，其中"有人→有人"那 9 对按人的关系再分两格 = 58。

这套验证回答的是**完备性**：规则覆盖了整个合法空间，没有哪一格缺处理。异常不是空间
外的东西，是空间里稀有的格子。
"""

from __future__ import annotations

from typing import Any

import pytest
from tests import door_link

from app.modules.doors import service

T0 = "2026-09-30T12:00:00.000Z"
T1 = "2026-09-30T12:01:00.000Z"

IDLE = ["free", "selected", "repair", "disabled"]
OCCUPIED = ["waiting", "bathing", "paused"]


def _reading(state: str, who: str = "A") -> dict[str, Any]:
    """构造一个能派生出指定状态的门条目。"""
    base: dict[str, Any] = {"door_no": 1, "card_no": 0, "cert_no": "", "acc_no": "", "bath_time_s": 0, "status_raw": 0}
    if state == "selected":
        base["status_raw"] = 2
    elif state == "repair":
        base["status_raw"] = 1
    elif state == "disabled":
        base["status_raw"] = 3
    elif state == "waiting":
        base["acc_no"] = who
    elif state == "bathing":
        base["acc_no"] = who
        base["in_time"] = T0
    elif state == "paused":
        base["acc_no"] = who
        base["bath_time_s"] = 100
        base["close_time"] = T0
    return base


def _frame(doors: list[dict[str, Any]], at: str) -> dict[str, Any]:
    return {"snapshot_id": f"cell-{at}", "source": service.SOURCE_DB_WATCH, "captured_at": at, "doors": doors}


# ── 构造 58 格 ───────────────────────────────────────────────────────

CASES: list[tuple[str, str, str, str]] = []
for f in IDLE:
    for t in IDLE:
        CASES.append((f, t, "same", "R1"))
for f in IDLE:
    for t in OCCUPIED:
        CASES.append((f, t, "new", "R2"))
for f in OCCUPIED:
    for t in IDLE:
        CASES.append((f, t, "gone", "R3"))
for f in OCCUPIED:
    for t in OCCUPIED:
        CASES.append((f, t, "same", "R4"))
        CASES.append((f, t, "other", "R5"))


def test_cell_count_matches_theorem() -> None:
    """转换的等价类恰好 58 格 = 16 + 12 + 12 + 9 + 9。"""
    from collections import Counter

    counts = Counter(rule for _, _, _, rule in CASES)
    assert counts == {"R1": 16, "R2": 12, "R3": 12, "R4": 9, "R5": 9}
    assert len(CASES) == 58


@pytest.mark.parametrize(("f_state", "t_state", "rel", "rule"), CASES)
def test_every_cell(fresh_db: str, f_state: str, t_state: str, rel: str, rule: str) -> None:
    """逐格验证：每一格的事件与会话动作都符合定稿。"""
    device_id = f"dev-cell-{f_state}-{t_state}-{rel}"

    # 第一帧把门置成 from 状态。占用者固定是 A，好让 R5 有得换。
    door_link.feed(device_id, _frame([_reading(f_state, "A")], T0))
    assert door_link.get_all_live(device_id)[1]["state"] == f_state, "首帧的状态派生要准确"

    # 首帧即占用会开会话；空闲态不开。这一条对 58 格全都成立。
    opened_after_first = len(door_link.list_sessions(device_id))
    assert opened_after_first == (1 if f_state in OCCUPIED else 0), "首帧：占用态开会话，空闲态不开"

    # 第二帧置成 to 状态。人按关系决定——R5 换成 B，其余保持 A。
    who = "B" if rel == "other" else "A"
    door_link.feed(device_id, _frame([_reading(t_state, who)], T1))
    assert door_link.get_all_live(device_id)[1]["state"] == t_state

    events = door_link.list_events(device_id)
    sessions = door_link.list_sessions(device_id)

    # 事件：状态变了才记一条，没变就一条都不记（首帧从不记）
    assert len(events) == (1 if f_state != t_state else 0), f"{f_state}→{t_state} 的事件数不对"
    if events:
        assert (events[0]["from_state"], events[0]["to_state"]) == (f_state, t_state)

    if rule == "R1":
        assert sessions == [], "R1 不涉及会话"
    elif rule == "R2":
        assert len(sessions) == 1
        # 这些格子的起点正是这一帧。倒推只在**确实在出水**时发生，而这几格的测试数据
        # 没有出水证据——所以断言取等号，它同时守着"没有意外的倒推"。
        assert sessions[0]["assigned_at"] == T1, "R2 的起点是这一帧"
    elif rule == "R3":
        assert len(sessions) == 1, "R3 只应当留下一条会话"
        assert sessions[0]["ended_at"] == T1, "R3 的终点是这一帧"
        assert sessions[0]["last_seen_at"] == T0, "last_seen_at 停在最后一次确认占用"
    elif rule == "R4":
        assert len(sessions) == 1
        assert sessions[0]["assigned_at"] == T0, "R4 沿用同一个会话"
        assert sessions[0]["last_seen_at"] == T1, "R4 推进 last_seen_at"
        assert sessions[0]["ended_at"] == ""
    elif rule == "R5":
        assert len(sessions) == 2, "R5 一关一开"
        assert sessions[0]["cert_no"] == "A"
        assert sessions[0]["ended_at"] == T1
        assert sessions[1]["cert_no"] == "B"
        assert sessions[1]["assigned_at"] == T1, "新账从这一帧起算"
    else:  # pragma: no cover - 规则名来自上面的常量
        message = f"未知规则 {rule}"
        raise AssertionError(message)
