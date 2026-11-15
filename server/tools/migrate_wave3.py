"""波次 3 一次性迁移：ota/voice/audit 三域底层收敛（跑一次验证后即删）。

四组变更（与 app 模型的最终形态对齐，见同 PR 的基线同步）：
① ota_releases：删 mandatory 死列（设备端从不读取）；
② ota_releases.target：逗号分隔文本 → JSON 数组（"all" → ["*"]）；
③ command_audits：列 result → data（与 device.command_result 帧、
   CommandOutcome.data 统一词汇）；
④ voice_deliveries：删 trace_id 死列（只写不读）；status 收敛为
   voice_delivery_status CHECK 闭集（pending/ok/failed/timeout/rejected）——
   该表按新形状重建（SQLite 不能对既有表追加 CHECK 约束）。

用法：对生产库的**已停服副本**执行
    python tools/migrate_wave3.py <db_path>
脚本自带防呆：形状不符即中止不做半截改动；全流程单事务，结束打印对账结果。
"""

from __future__ import annotations

import json
import sqlite3
import sys

DELIVERY_STATUSES = ("pending", "ok", "failed", "timeout", "rejected")

VOICE_DELIVERIES_NEW = """
CREATE TABLE voice_deliveries_new (
    id INTEGER NOT NULL PRIMARY KEY,
    event_key VARCHAR(128) NOT NULL,
    rule_id INTEGER NOT NULL,
    device_id VARCHAR(64),
    door_no INTEGER NOT NULL,
    cert_no VARCHAR(32) NOT NULL,
    event VARCHAR(32) NOT NULL,
    segments TEXT NOT NULL,
    segment_count INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    occurred_at DATETIME NOT NULL,
    dispatched_at DATETIME,
    done_at DATETIME,
    downloaded BOOLEAN NOT NULL,
    status VARCHAR(32) NOT NULL,
    detail TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    CHECK (status IN ('pending', 'ok', 'failed', 'timeout', 'rejected')),
    FOREIGN KEY (rule_id) REFERENCES voice_rules (id) ON DELETE RESTRICT,
    UNIQUE (event_key)
)
"""


def _columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({table})")]


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(f"中止：{message}", file=sys.stderr)
        sys.exit(1)


def migrate(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=OFF")
    con.isolation_level = None  # 手动管理事务
    con.execute("BEGIN")

    try:
        # ① ota_releases.mandatory
        cols = _columns(con, "ota_releases")
        _require("mandatory" in cols, "ota_releases 没有 mandatory 列（已迁移过？）")
        con.execute("ALTER TABLE ota_releases DROP COLUMN mandatory")

        # ② ota_releases.target：逗号文本 → JSON 数组
        _require("target" in cols, "ota_releases 没有 target 列")
        rows = con.execute("SELECT version, target FROM ota_releases").fetchall()
        for version, raw in rows:
            value = str(raw).strip()
            if value == "all":
                new = ["*"]
            else:
                new = [item.strip() for item in value.split(",") if item.strip()]
                _require(bool(new), f"ota_releases.target 解析不出目标（version={version!r}, 原值={raw!r}）")
            con.execute(
                "UPDATE ota_releases SET target = ? WHERE version = ?", (json.dumps(new, ensure_ascii=False), version)
            )

        # ③ command_audits.result → data
        cols = _columns(con, "command_audits")
        _require("result" in cols and "data" not in cols, "command_audits 列形状与预期不符（已迁移过？）")
        con.execute("ALTER TABLE command_audits RENAME COLUMN result TO data")

        # ④ voice_deliveries：删 trace_id + status 闭集 CHECK（整表重建）
        cols = _columns(con, "voice_deliveries")
        _require("trace_id" in cols, "voice_deliveries 列形状与预期不符（已迁移过？）")
        bad = con.execute(
            "SELECT COUNT(*) FROM voice_deliveries WHERE status NOT IN (?, ?, ?, ?, ?)", DELIVERY_STATUSES
        ).fetchone()[0]
        _require(bad == 0, f"voice_deliveries 有 {bad} 行 status 超出闭集，先人工处理再迁移")
        count_before = con.execute("SELECT COUNT(*) FROM voice_deliveries").fetchone()[0]
        con.execute("DROP INDEX IF EXISTS ix_voice_deliveries_device_id")
        con.execute("DROP INDEX IF EXISTS ix_voice_deliveries_occurred_at")
        con.execute("ALTER TABLE voice_deliveries RENAME TO voice_deliveries_old")
        con.execute(VOICE_DELIVERIES_NEW)
        con.execute(
            """
            INSERT INTO voice_deliveries_new
                (id, event_key, rule_id, device_id, door_no, cert_no, event, segments,
                 segment_count, duration_ms, occurred_at, dispatched_at, done_at,
                 downloaded, status, detail, created_at)
            SELECT id, event_key, rule_id, device_id, door_no, cert_no, event, segments,
                 segment_count, duration_ms, occurred_at, dispatched_at, done_at,
                 downloaded, status, detail, created_at
            FROM voice_deliveries_old
            """
        )
        con.execute("DROP TABLE voice_deliveries_old")
        con.execute("ALTER TABLE voice_deliveries_new RENAME TO voice_deliveries")
        con.execute("CREATE INDEX ix_voice_deliveries_device_id ON voice_deliveries (device_id)")
        con.execute("CREATE INDEX ix_voice_deliveries_occurred_at ON voice_deliveries (occurred_at)")

        # ⑤ 对账
        cols = _columns(con, "ota_releases")
        _require("mandatory" not in cols, "对账失败：mandatory 仍存在")
        _require(_columns(con, "command_audits").count("data") == 1, "对账失败：command_audits.data 缺失")
        _require("trace_id" not in _columns(con, "voice_deliveries"), "对账失败：trace_id 仍存在")
        count_after = con.execute("SELECT COUNT(*) FROM voice_deliveries").fetchone()[0]
        _require(count_before == count_after, f"对账失败：voice_deliveries 行数 {count_before} → {count_after}")

        con.execute("COMMIT")
        print(f"迁移完成：ota_releases {len(rows)} 行 target 已转 JSON；voice_deliveries {count_after} 行完整；事务已提交。")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python tools/migrate_wave3.py <db_path>", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1])
