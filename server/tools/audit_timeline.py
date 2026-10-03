"""核对时间轴与派生结果：入库前后、改造前后、生产与副本各跑一遍。

## 它回答什么

- 时间**格式与范围**对不对（RFC 3339、无空值、无坏值）；
- **两条轴**分得清不清（`captured_at` 是服务端轴，`device_clock` 是设备钟读数）；
- 派生结果有没有**越界**（段落在会话区间之外、段长越过一次出水的物理上限）；
- 质量维度（完整性、连续性、起点来源）的分布；
- **历史导入与实时上报的接缝**：`import` 帧不带设备钟读数（旧系统没有那一列），
  实时帧带——两者在 `device_clock` 上的分布就是那条缝的位置。

## 什么时候跑

入库之后、改推导规则之后、以及**对着生产库做任何操作之前**。它只读，不改任何东西。

    用法:

    cd server
    python -m tools.audit_timeline --db data/ntu-shower.db
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

#: RFC 3339 的形状：日期 + T + 时刻。只验形状，值域交给 SQLite 的字符串比较。
_SHAPE = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*"

#: 一次出水的物理上限，秒。与 service.MAX_SEGMENT_S 同源；这里写死是为了让本工具
#: 不依赖被测代码——它存在的意义正是独立地看一眼结果。
MAX_SEGMENT_S = 1200


def _one(conn: sqlite3.Connection, sql: str) -> tuple[object, ...]:
    row = conn.execute(sql).fetchone()
    return tuple(row) if row is not None else ()


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def audit(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    problems: list[str] = []

    print(f"库：{db_path}")
    print()
    print("── 原始层 ────────────────────────────────────────────────")
    print(f"  帧数            {_count(conn, 'SELECT COUNT(*) FROM door_snapshot')}")
    low, high = _one(conn, "SELECT MIN(captured_at), MAX(captured_at) FROM door_snapshot")
    print(f"  captured_at     {low}  →  {high}")
    for source, count in conn.execute("SELECT source, COUNT(*) FROM door_snapshot GROUP BY source"):
        print(f"  来源 {source:<10} {count}")
    empty_captured = _count(conn, "SELECT COUNT(*) FROM door_snapshot WHERE captured_at IS NULL OR captured_at = ''")
    print(f"  captured 为空   {empty_captured}")
    bad = _count(conn, f"SELECT COUNT(*) FROM door_snapshot WHERE captured_at NOT GLOB '{_SHAPE}'")
    print(f"  captured 形状坏 {bad}")
    if bad:
        problems.append(f"原始层有 {bad} 帧的 captured_at 形状不对")
    with_clock = _count(
        conn, "SELECT COUNT(*) FROM door_snapshot WHERE device_clock IS NOT NULL AND device_clock <> ''"
    )
    frames = _count(conn, "SELECT COUNT(*) FROM door_snapshot")
    print(f"  device_clock    {with_clock} / {frames} 帧带读数")
    clock_bad = _count(
        conn, f"SELECT COUNT(*) FROM door_snapshot WHERE device_clock <> '' AND device_clock NOT GLOB '{_SHAPE}'"
    )
    if clock_bad:
        problems.append(f"原始层有 {clock_bad} 帧的 device_clock 形状不对")

    print()
    print("── 派生层：会话 ──────────────────────────────────────────")
    sessions = _count(conn, "SELECT COUNT(*) FROM usage_session")
    print(f"  会话            {sessions}")
    low, high = _one(conn, "SELECT MIN(assigned_at), MAX(assigned_at) FROM usage_session")
    print(f"  起点范围        {low}  →  {high}")
    low, high = _one(conn, "SELECT MIN(ended_at), MAX(ended_at) FROM usage_session")
    print(f"  终点范围        {low}  →  {high}")
    open_sessions = _count(conn, "SELECT COUNT(*) FROM usage_session WHERE ended_at IS NULL")
    print(f"  未结束          {open_sessions}")
    for column in ("assigned_source", "observed_span", "segments_complete"):
        pairs = ", ".join(
            f"{row[0]}={row[1]}"
            for row in conn.execute(f"SELECT {column}, COUNT(*) FROM usage_session GROUP BY {column}")
        )
        print(f"  {column:<16}{pairs}")

    print()
    print("── 派生层：段 ────────────────────────────────────────────")
    segments = _count(conn, "SELECT COUNT(*) FROM usage_segment")
    print(f"  段              {segments}")
    low, high = _one(conn, "SELECT MIN(opened_at), MAX(opened_at) FROM usage_segment")
    print(f"  起点范围        {low}  →  {high}")
    pairs = ", ".join(
        f"{row[0] or '(在途)'}={row[1]}"
        for row in conn.execute("SELECT source, COUNT(*) FROM usage_segment GROUP BY source")
    )
    print(f"  来源            {pairs}")

    checks = [
        (
            "段起点为空",
            "SELECT COUNT(*) FROM usage_segment WHERE opened_at IS NULL OR opened_at = ''",
        ),
        (
            "段长越过上限",
            f"SELECT COUNT(*) FROM usage_segment WHERE flow_s > {MAX_SEGMENT_S}",
        ),
        (
            "段起点早于会话起点",
            "SELECT COUNT(*) FROM usage_segment g JOIN usage_session s ON s.id = g.session_id"
            " WHERE g.opened_at < s.assigned_at",
        ),
        (
            "段终点晚于会话终点",
            "SELECT COUNT(*) FROM usage_segment g JOIN usage_session s ON s.id = g.session_id"
            " WHERE s.ended_at IS NOT NULL AND g.closed_at IS NOT NULL AND g.closed_at > s.ended_at",
        ),
        (
            "在途段仍带着长度",
            "SELECT COUNT(*) FROM usage_segment WHERE closed_at IS NULL AND flow_s <> 0",
        ),
        (
            "会话起点晚于终点",
            "SELECT COUNT(*) FROM usage_session WHERE ended_at IS NOT NULL AND assigned_at > ended_at",
        ),
    ]
    for label, sql in checks:
        count = _count(conn, sql)
        print(f"  {label:<16}{count}")
        if count:
            problems.append(f"{label}：{count} 处")

    print()
    if problems:
        print("结论：有问题")
        for problem in problems:
            print(f"  ✗ {problem}")
        return 1
    print("结论：时间轴与派生结果自洽（无空值、无坏形状、无越界）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核对某份库的时间轴与派生结果")
    parser.add_argument("--db", required=True, help="要核对的库；只读，不改动")
    args = parser.parse_args(argv)
    return audit(Path(args.db))


if __name__ == "__main__":
    raise SystemExit(main())
