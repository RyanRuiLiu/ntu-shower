"""把 archive 里的历史原始帧重放进当前库。

## 为什么要有它

历史帧来自旧系统（`ntu-remote-shower`）的 `door_snapshots` 表，那是唯一一份逐帧存留的
记录（100 天共 35,861 帧）。派生层（会话、状态迁移）**不复用任何既有结论**：它们由当前
代码重算，因此推导规则一经改进，历史数据随之更新，不会残留按旧口径算出的数值。

## archive 里那几份是什么

均为 SQLite，直接打开即可。它们在版本控制之外（见 `.gitignore`）——二进制大文件入库
会让仓库永久膨胀。内容早已进新库，但**原始形态有独立价值**：新库中的帧是转换后的
（列名 snake_case、时刻 RFC 3339、无值不出现键），而这里是设备与旧系统的原话。将来若
需换一种解析方式重来，或核对某条结论，原始库比转换后的帧可靠。

| 文件 | 内容 | 覆盖期间 |
|---|---|---|
| `shower.db` | 旧系统的库。其中 `door_snapshots` 是本工具的唯一输入 | 至 2026-07-08 |
| `bath-backup-20260708.db` | 设备端浴室库 | 2020-12-28 → 2026-07-08 |
| `bath-backup-20260708.db.gz` | 同一份的压缩版（设备侧压缩后传输的形态） | 同上 |
| `bath-live-20261002.db` | 设备端活跃库的快照，每日变化 | 2026-07-08 → 10-02 |
| `ntu-shower-prod-20261002.db.gz` | 本项目上一版的生产备份，无原始层，仅含其算出的会话与事件 | 2026-09-30 → 10-02 |

设备端那两份里有两张表可作**抽样验算**（不是对账基准）：

- `t_recerrs` 是结算流水，独立于本系统的采集链路。可取其 `txtime − openTime` 与会话
  跨度比对，或取 `txamt ÷ 费率` 与 `flow_s` 比对。实测抽两天对齐 97.9%、时刻中位差
  4 秒、金额比值误差 0.4%（设备费率 `rate = 14.0 元/小时`，即 0.3889 分/秒）。它天然
  到不了 100%：免费用户、数十秒的短会话、同一喷头 6 秒内的两次连续操作都不入表。
- `t_fastinoutdoorinfo` 是设备自己记录的"快速进出"，可与本系统的短会话口径对照。

## 输入从哪来

`shower.db` 只有一张有内容的表：

- `door_snapshots.received_at` —— 服务端收到那一刻，**epoch 秒整数**（换算见 [_load_frames]）；
- `door_snapshots.doors_json` —— 设备那一行的原文（`camelCase`）。

## 转换规则

字段名换成协议的形状（`doorNo` → `door_no`、`bathTime` → `bath_time_s`，余同）；三个时间
字段从 **epoch 秒**换成 RFC 3339，**0 表示"没有"，此时该键不出现**——协议中"键不存在"
即此意，代入 1970 年会让所有判据失真。

## 时刻取哪一侧

`captured_at` 一律取历史的 `received_at`，否则 100 天的活动会全部堆积到重放这一刻。旧
系统不存在采集侧的问题：其服务端与设备同机，收到即采集，两者相差在微秒级。

用法：

    cd server
    python -m tools.replay_history --source ../archive/shower.db --target data/replayed.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: 历史归属的设备。旧系统用 `ntu-shower-001` 这个自造标识，而现网设备是它的
#: 安卓 ID——重放要落到同一台设备上，否则会话会挂在两条互不相干的记录下。
DEVICE_ID = "<device-id>"

#: 旧库 → 协议的字段名对照。三个时间字段单独处理（见模块头）。
_FIELDS = {
    "doorNo": "door_no",
    "cardno": "card_no",
    "certno": "cert_no",
    "accno": "acc_no",
    "bathTime": "bath_time_s",
    "status": "status_raw",
}

#: epoch 秒 → RFC 3339 的时刻字段。0 表示"没有"，此时键不出现。
_MOMENTS = ("openTime", "inTime", "closeTime")
_MOMENT_KEYS = {"openTime": "open_time", "inTime": "in_time", "closeTime": "close_time"}


def _to_rfc3339(epoch_s: object) -> str | None:
    """epoch 秒 → RFC 3339 UTC 毫秒；0 或坏值返回 None（调用方据此省略该键）。

    格式化经 core.clock（AGENTS.md 规定它是时间的唯一来源）。本工具的导入时机早于
    app，所以这一处的导入留在调用时。
    """
    from app.core.clock import format_rfc3339

    if not isinstance(epoch_s, int) or epoch_s <= 0:
        return None
    return format_rfc3339(datetime.fromtimestamp(epoch_s, timezone.utc))


def _adapt(raw: dict[str, Any]) -> dict[str, Any]:
    """旧库的一个门条目 → 协议形状。"""
    door: dict[str, Any] = {}
    for old, new in _FIELDS.items():
        value = raw.get(old)
        if value is None:
            continue
        door[new] = value
    for old in _MOMENTS:
        shifted = _to_rfc3339(raw.get(old))
        if shifted is not None:
            door[_MOMENT_KEYS[old]] = shifted
    return door


def _load_frames(source: Path) -> list[tuple[str, str, str]]:
    """读原始帧，返回 `(captured_at, doors_json, snapshot_id)` 的列表。

    **旧库的 `received_at` 是 epoch 秒整数**，不是 RFC 3339——要在这里换过来。漏了这
    一步的后果不是报错而是静默走偏：`parse_rfc3339` 解析不了就退回"重放这一刻"，于是
    100 天的活动全堆到同一秒上。

    `snapshot_id` 用旧库的主键拼出来：原样保留它，是为了让"这一帧对应旧系统的哪一行"
    永远查得回去。
    """
    conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    rows = conn.execute("SELECT id, received_at, doors_json FROM door_snapshots ORDER BY received_at, id").fetchall()
    conn.close()

    frames: list[tuple[str, str, str]] = []
    for row_id, epoch_s, blob in rows:
        moment = _to_rfc3339(epoch_s)
        if moment is not None:
            frames.append((moment, blob, f"legacy-{row_id}"))
    return frames


def turbo_writes(conn: sqlite3.Connection) -> None:
    """把重放期间的写调成批处理。

    默认路径每写一条语句就提交一次——实时场景下那是必须的（每个 commit 都落盘，掉电
    不丢），而重放 35,861 帧会发出六十万次左右的提交，每次等一次 fsync，于是几分钟的
    活拖成半小时。

    重放是**离线批处理、单线程、原始帧在别处另有副本**，没有要保护的东西：日志放内存、
    关掉同步。提交时机由调用方掌握（重放主体不发提交，末尾统一落一次）。实时路径不走
    这里。

    页缓存开到 64 MB：库本身只有几十 MB，这样绝大部分读都不落盘。临时表也放内存——
    排序与去重不必经过磁盘。
    """
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")


def replay(source: Path, target: Path) -> dict[str, int]:
    """把 source 里的帧重放进 target 的库。target 已存在时先清空它的历史部分。"""
    # 项目代码要在目标库上工作，所以先把 settings 指过去再导入 app
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.core import db as core_db
    from app.core import device_clock
    from app.core.config import settings

    settings.db_path = str(target)
    device_clock.clear()

    from app.main import init_schema
    from app.modules.doors import repository, service

    init_schema()
    _trim(target)
    conn = core_db.get_conn()
    turbo_writes(conn)

    # 重放主体不发提交：把 repository 的 write 换成不提交的版本。数十万次提交只有
    # 这样才收敛得住。改的是模块属性（而非 core_db.write）——repository 中是
    # `from ... import write` 绑定的名字，替换源头它不认。
    def deferred(sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        return conn.execute(sql, params)

    setattr(repository, "write", deferred)  # noqa: B010 - 理由见上

    # 关掉事件发布。每帧都要刷新一遍"每个门的最新会话"来组装载荷，而重放时根本没有
    # 订阅者——这一项单独占到全部耗时的四成。
    from app.core import events as events_module

    setattr(events_module, "publish", lambda *_args, **_kwargs: None)  # noqa: B010 - 同上

    frames = _load_frames(source)
    last_doors: str | None = None
    duplicates = 0
    for index, (at, blob, snapshot_id) in enumerate(frames, 1):
        adapted = [_adapt(item) for item in json.loads(blob)]
        if blob == last_doors:
            # 与紧邻的前一帧相同：设备重发，不携带新信息。**只落原始层**——它是"这一刻
            # 确实收到了这一幕"的事实；**不进推导**，重发一条"空闲"会被读成"刚刚变为
            # 空闲"，将一段占用切成两段。
            #
            # 也不能走 process_snapshot 那条路：它会刷新 door_live 的 reported_at，而
            # 帧间隔正是依据该列计算——中间插入一帧重复的，间隔即被压小，`flow_s` 中
            # "上一帧到本帧"那一段随之缩短（实测 100 天里合计少 21891 秒）。
            duplicates += 1
            repository.insert_snapshot(DEVICE_ID, snapshot_id, None, service.SOURCE_IMPORT, at, at, adapted)
            continue
        last_doors = blob
        service.process_snapshot(
            DEVICE_ID,
            {
                "snapshot_id": snapshot_id,
                "source": service.SOURCE_IMPORT,
                # 历史的采集时刻就是它的接收时刻（理由见模块头）
                "captured_at": at,
                "doors": adapted,
            },
        )
        if index % 5000 == 0:
            print(f"  ... {index} / {len(frames)}")

    # 批处理期间没提交过，收尾前先落盘——后面那两个函数各开自己的连接，看不见未提交的写
    core_db.get_conn().commit()
    settled = settle_open_sessions(target)
    return {"frames": len(frames), "duplicates": duplicates, "settled": settled}


def _trim(target: Path) -> None:
    """清空历史部分。重放是幂等的：重跑一遍得到的是同一份结果，不是叠加。"""
    conn = sqlite3.connect(target)
    for table in ("door_snapshot", "usage_session", "door_state_event"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def settle_open_sessions(target: Path) -> int:
    """收尾：重放结束时仍开着的会话，按"最后见到"那一刻结束。

    数据总在某一刻截断，而截断时恰好有人在洗是常态。不收尾的话它们会永远挂在
    "进行中"，让每一个"当前占用"的统计都多算一条。
    """
    conn = sqlite3.connect(target)
    rows = conn.execute("SELECT id, last_seen_at FROM usage_session WHERE ended_at IS NULL").fetchall()
    for session_id, last_seen in rows:
        conn.execute("UPDATE usage_session SET ended_at = ? WHERE id = ?", (last_seen, session_id))
    conn.commit()
    conn.close()
    return len(rows)


def _count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把历史原始帧重放进当前库")
    parser.add_argument("--source", required=True, help="旧系统的库（含 door_snapshots 表）")
    parser.add_argument("--target", required=True, help="目标库；已有历史会被清空后重建")
    args = parser.parse_args(argv)

    stats = replay(Path(args.source), Path(args.target))
    print(f"读到 {stats['frames']} 帧，其中紧邻重复 {stats['duplicates']} 帧（只落原始层、不进推导）")
    print(f"收尾未结束会话 {stats['settled']} 条")

    conn = sqlite3.connect(f"file:{args.target}?mode=ro", uri=True)
    for table in ("door_snapshot", "usage_session", "door_state_event"):
        print(f"  {table:<18} {_count(conn, f'SELECT COUNT(*) FROM {table}')} 行")
    print(f"  flow_s 合计 {_count(conn, 'SELECT SUM(flow_s) FROM usage_session')}")
    print(f"  未结束会话 {_count(conn, 'SELECT COUNT(*) FROM usage_session WHERE ended_at IS NULL')}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
