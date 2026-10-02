"""从本库的原始层重算派生层。

## 为什么要有它

`door_snapshot` 是原始层——设备说了什么就记什么；`door_live`、`door_state_event`、
`usage_session` 是由它推出来的。**推导规则改了，历史就得跟着重算**，否则库里会同时
存在两套口径算出的数值，而它们从外表看没有区别。原始层存在的全部意义就在这里。

`tools/replay_history.py` 做的是另一件事：把旧系统的库灌进来。本工具处理的是**本库
已有的帧**——包括那些当年按旧规则处理过的。

## 什么时候跑

- 推导规则改进之后，新口径要覆盖全部历史；
- 发现某类帧被错待、需要补算。2026-10-02 门 4 就是这样：重连推的全貌帧当时被整帧
  跳过派生，那一场（20:03:25 刷卡、20:11:44 关阀、出水 449 秒）没被开出来。

## 偏差表怎么给

`_assigned_at` 与 `_opened_at` 会把设备报的时刻加上钟偏差平移，而**偏差是随时间变的**。
帧的两个来源在这一点上截然不同：

- `import` 帧来自旧系统那 100 天，当时的偏差与今天无关，**一律按无偏差处理**——与当年
  重放时一致，于是那批会话的结论逐字不变；
- 实时帧（`db_watch` / `startup`）与当前偏差同处一个时间尺度，用 `device.clock_skew_s`
  里存着的那份。

切换点是**第一帧非 import**：在那之前清空，之后载入。

## 幂等

派生层先清空再重建，跑两遍结果相同。`door_snapshot` 一行不动——它是输入，不是产物。

用法：

    cd server
    python -m tools.rebuild_derived --db data/ntu-shower.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

#: 派生层三张表。原始层 `door_snapshot` 不在其中——它是输入，不是产物。
_DERIVED = ("door_live", "door_state_event", "usage_session")


def _load_frames(conn: sqlite3.Connection) -> list[tuple[str, str, str, str, str]]:
    """原始层全部帧，返回 `(device_id, captured_at, source, doors, snapshot_id)`。

    排序用 `captured_at` 而不是 `id`：`import` 帧是今天才写进来的，描述的是 100 天前，
    按 id 排会把历史整个堆到实时帧后面。
    """
    rows = conn.execute(
        "SELECT device_id, captured_at, source, doors, snapshot_id FROM door_snapshot"
        " WHERE captured_at IS NOT NULL AND captured_at <> ''"
        " ORDER BY captured_at, id"
    ).fetchall()
    return [(row[0], row[1], row[2], row[3], row[4]) for row in rows]


def _clear_derived(conn: sqlite3.Connection) -> None:
    """清空派生层。`door_live` 也要清——留着旧行会让第一帧读到并不存在的"上一帧"。"""
    for table in _DERIVED:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def _stored_skew(conn: sqlite3.Connection) -> float:
    """库里存着的设备钟偏差。"""
    row = conn.execute("SELECT clock_skew_s FROM device LIMIT 1").fetchone()
    return float(row[0]) if row is not None else 0.0


def _doors_of(blob: str) -> list[object]:
    """原始层的 `doors` 列 → 协议载荷里的门列表。"""
    parsed = json.loads(blob)
    return parsed if isinstance(parsed, list) else []


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def rebuild(db_path: Path) -> dict[str, int]:
    """就地按原始层重算派生层。"""
    # 项目代码要在目标库上工作，所以先把 settings 指过去再导入 app
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.core import db as core_db
    from app.core import device_clock
    from app.core.config import settings

    settings.db_path = str(db_path)

    from tools.replay_history import settle_open_sessions, turbo_writes

    from app.main import init_schema
    from app.modules.doors import repository, service

    init_schema()
    conn = core_db.get_conn()
    frames = _load_frames(conn)
    skew = _stored_skew(conn)

    _clear_derived(conn)
    turbo_writes(conn)

    # 重建主体不发提交：每帧一次 commit 会把几万帧拖成几十分钟（理由见 replay_history）。
    # 改的是模块属性——repository 里是 `from ... import write` 绑定的名字，换源头它不认。
    def deferred(sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        return conn.execute(sql, params)

    setattr(repository, "write", deferred)  # noqa: B010 - 理由见上
    # 帧已经在原始层里，重放不该再插一遍（唯一索引会直接拒绝）。
    setattr(repository, "insert_snapshot", lambda *_args, **_kwargs: None)  # noqa: B010 - 同上

    # 关掉事件发布：重放时没有订阅者，而每帧都要组装一次完整现态，代价不菲。
    from app.core import events as events_module

    setattr(events_module, "publish", lambda *_args, **_kwargs: None)  # noqa: B010 - 同上

    device_clock.clear()
    in_import = True
    last_import_doors = ""
    processed = 0
    skipped = 0

    for device_id, at, source, doors, snapshot_id in frames:
        if source == service.SOURCE_IMPORT:
            if not in_import:
                device_clock.clear()
                in_import = True
            # 与紧邻的前一帧相同：当年重放时就不进推导（理由见 replay_history），
            # 重建要复现同一份结论，这一步不能省。
            if doors == last_import_doors:
                skipped += 1
                continue
            last_import_doors = doors
        elif in_import:
            device_clock.load(device_id, skew)
            in_import = False

        service.process_snapshot(
            device_id,
            {
                "snapshot_id": snapshot_id,
                "source": source,
                "captured_at": at,
                "doors": _doors_of(doors),
            },
        )
        processed += 1

    conn.commit()
    settled = settle_open_sessions(db_path)

    stats = {table: _count(conn, f"SELECT COUNT(*) FROM {table}") for table in _DERIVED}
    return {
        "frames": len(frames),
        "processed": processed,
        "skipped": skipped,
        "settled": settled,
        "flow_s": _count(conn, "SELECT COALESCE(SUM(flow_s), 0) FROM usage_session"),
        **stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从原始层重算派生层")
    parser.add_argument("--db", required=True, help="目标库；它的派生层会被清空后重建")
    args = parser.parse_args(argv)

    stats = rebuild(Path(args.db))
    print(f"读到 {stats['frames']} 帧，推导 {stats['processed']} 帧，跳过紧邻重复 {stats['skipped']} 帧")
    print(f"收尾未结束会话 {stats['settled']} 条")
    for table in _DERIVED:
        print(f"  {table:<18} {stats[table]} 行")
    print(f"  flow_s 合计 {stats['flow_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
