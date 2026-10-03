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

`_assigned_at` 与 `_maintain_segment` 会把设备报的时刻加上钟偏差平移，而**偏差是随时间变的**。
帧的两个来源在这一点上截然不同：

- `import` 帧来自旧系统那 100 天，当时的偏差与今天无关，**一律按无偏差处理**——与当年
  重放时一致，于是那批会话的结论逐字不变；
- 实时帧（`db_watch` / `startup`）与当前偏差同处一个时间尺度，而它**自己带着采集那一刻
  的设备钟读数**：拿它与 `captured_at` 相减就是当时的偏差，比 `device.clock_skew_s` 里
  存着的现值更贴。所以重建时逐帧刷新，与实时链路走同一条路。

切换点是**第一帧非 import**：在那之前清空，之后载入。

## 幂等

派生层先清空再重建，跑两遍结果相同。`door_snapshot` 一行不动——它是输入，不是产物。

## 为什么不收尾

`replay_history` 重放到历史截止、之后没有帧了，所以要把还开着的会话按"最后见到"结掉。
本库的帧是**连续的**：重建完服务立刻接着收，悬空的那条正是"此刻真有人在洗"。收尾会把
它提前结束——2026-10-02 试跑时，门 6 一条仍在进行的会话就被这么结掉了。

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

#: 派生层四张表。原始层 `door_snapshot` 不在其中——它是输入，不是产物。
_DERIVED = ("door_live", "door_state_event", "usage_session", "usage_segment")

#: 偏差跳变阈值：单次观测与当前值差得超过它就**直接采信新值**、不做平滑。与链路层
#: （`devices` 的 `update_clock_skew`）取自同一个配置项的默认值——它管的是"设备钟被动了"，
#: 与走哪条通路无关。
CLOCK_JUMP_THRESHOLD_S = 60


def _load_frames(conn: sqlite3.Connection) -> list[tuple[str, str, str, str, str, str]]:
    """原始层全部帧，返回 `(device_id, captured_at, source, device_clock, doors, snapshot_id)`。

    排序用 `captured_at` 而不是 `id`：`import` 帧是今天才写进来的，描述的是 100 天前，
    按 id 排会把历史整个堆到实时帧后面。
    """
    rows = conn.execute(
        "SELECT device_id, captured_at, source, device_clock, doors, snapshot_id FROM door_snapshot"
        " WHERE captured_at IS NOT NULL AND captured_at <> ''"
        " ORDER BY captured_at, id"
    ).fetchall()
    return [(row[0], row[1], row[2], row[3], row[4], row[5]) for row in rows]


def _drop_derived(conn: sqlite3.Connection) -> None:
    """丢掉派生层，连表一起。

    不能只 `DELETE FROM`：推导规则的改动常常伴随列的变化（会话表删过一列、加过三列），
    而 `CREATE TABLE IF NOT EXISTS` 不会去改一张已经存在的表——留下的旧结构会让重算在
    第一帧就撞上"没有这一列"。派生层的每个字节都能从原始层重算出来，所以丢掉它是安全的，
    这正是分层存在的意义。

    `door_live` 尤其要丢：留着旧行会让第一帧读到并不存在的"上一帧"。
    """
    for table in _DERIVED:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()


def _stored_skew(conn: sqlite3.Connection, device_id: str) -> float:
    """库里存着的**这台设备**的钟偏差。

    必须按设备取：偏差随设备而异（每台设备的钟各拨各的），拿任意一条去给所有设备
    当现值，会让第二台设备的第一帧用一个错偏差平移——而那一帧正是会话起点与
    "从痕迹推回开阀时刻"的输入。
    """
    row = conn.execute("SELECT clock_skew_s FROM device WHERE device_id = ?", (device_id,)).fetchone()
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

    from tools.replay_history import turbo_writes

    from app.main import init_schema
    from app.modules.doors import repository, service

    # 派生层连同**表结构**一起丢掉再建（理由见 _drop_derived）：结构变更与规则变更常常
    # 一起来，而 `CREATE TABLE IF NOT EXISTS` 对已存在的表什么都不做。
    conn = core_db.get_conn()
    _drop_derived(conn)
    init_schema()
    conn = core_db.get_conn()  # init_schema 可能换过连接，重取一次
    frames = _load_frames(conn)

    turbo_writes(conn)

    # 重建主体不发提交：每帧一次 commit 会把几万帧拖成几十分钟（理由见 replay_history）。
    # 改的是模块属性——repository 里是 `from ... import write` 绑定的名字，换源头它不认。
    def deferred(sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        return conn.execute(sql, params)

    setattr(repository, "write", deferred)  # noqa: B010 - 理由见上
    # 帧已经在原始层里，重放不该再插一遍（唯一索引会直接拒绝）。
    setattr(repository, "insert_snapshot", lambda *_args, **_kwargs: None)  # noqa: B010 - 同上

    # 关掉事件发布：重放时没有订阅者，而每帧都要组装一次完整现态，代价不菲。
    #
    # **换掉 `publish` 还不够**：`list_doors(device_id)` 是它的实参，会先被求值。而
    # `latest_usage_by_door` 要扫 `usage_segment` 全表，于是每帧一次全表扫描——帧数乘
    # 段数是 O(n²)。把 `list_doors` 也换掉才真的省下来。
    from app.core import events as events_module

    setattr(events_module, "publish", lambda *_args, **_kwargs: None)  # noqa: B010 - 同上
    setattr(service, "list_doors", lambda *_args, **_kwargs: [])  # noqa: B010 - 同上

    device_clock.clear()
    in_import = True
    last_import_doors = ""
    processed = 0
    skipped = 0

    for device_id, at, source, device_clock_text, doors, snapshot_id in frames:
        if source == service.SOURCE_IMPORT:
            in_import = True
            # 旧系统的库在同机上由它自己的服务端写，那些时刻**已经是服务端轴**，偏差恒为 0。
            # 这里必须 `load(0.0)` 而不是 `clear()`：`clear()` 让这台设备在偏差表里没有条目，
            # `to_server` 于是返回 None，调用方退回"本帧观测时刻"——而采到 `in_time` 的那一帧
            # 可能比 `in_time` 晚一小时（`bathTime` 还在累加，说明中间关过阀）。36 万帧里有
            # 36 次，落在本地时已越过午夜，看起来像"凌晨有人在洗澡"。
            device_clock.load(device_id, 0.0)
            # 与紧邻的前一帧相同：当年重放时就不进推导（理由见 replay_history），
            # 重建要复现同一份结论，这一步不能省。
            if doors == last_import_doors:
                skipped += 1
                continue
            last_import_doors = doors
        elif in_import:
            # 按设备取，不用循环外算好的那一份——偏差随设备而异。这里每台设备只走一次。
            device_clock.load(device_id, _stored_skew(conn, device_id))
            in_import = False

        # 实时帧自己带着采集那一刻的设备钟读数（与 captured_at 同刻），偏差由它算出来，
        # 比 `device.clock_skew_s` 里存着的现值更贴当时。阈值与链路层用的是同一个配置项
        # 的默认值：跳变阈值管的是"设备钟被动了"，与走哪条通路无关。
        if isinstance(device_clock_text, str) and device_clock_text:
            device_clock.update(device_id, device_clock_text, at, CLOCK_JUMP_THRESHOLD_S)

        service.process_snapshot(
            device_id,
            {
                "snapshot_id": snapshot_id,
                "source": source,
                "captured_at": at,
                "device_clock": device_clock_text or "",
                "doors": _doors_of(doors),
            },
        )
        processed += 1

    conn.commit()

    stats = {table: _count(conn, f"SELECT COUNT(*) FROM {table}") for table in _DERIVED}
    return {
        "frames": len(frames),
        "processed": processed,
        "skipped": skipped,
        "flow_s": _count(conn, "SELECT COALESCE(SUM(flow_s), 0) FROM usage_session"),
        **stats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从原始层重算派生层")
    parser.add_argument("--db", required=True, help="目标库；它的派生层会被清空后重建")
    args = parser.parse_args(argv)

    stats = rebuild(Path(args.db))
    print(f"读到 {stats['frames']} 帧，推导 {stats['processed']} 帧，跳过紧邻重复 {stats['skipped']} 帧")
    for table in _DERIVED:
        print(f"  {table:<18} {stats[table]} 行")
    print(f"  flow_s 合计 {stats['flow_s']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
