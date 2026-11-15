"""波次 1 一次性搬迁：device_configs / device_clock_shifts 并入 devices 影子行。

用途与生命周期见 CONTRIBUTING 的重写流程：跑一次、验证、删除。只依赖标准库，
可在任意 python:3.13 容器里对库文件直跑：

    python tools/migrate_wave1_device_shadow.py /path/to/ntu-shower.db

动作：
1. devices 增加 5 列（配置覆盖 2、拨钟态 2、停用位 1）；
2. 两张旧表的一行一设备数据搬进影子行后删表；
3. 四张历史表的外键 CASCADE 改 RESTRICT（表重建，索引随迁）。
幂等性不提供——跑两次第二次会在 ADD COLUMN 处报重复列，这正是想要的防呆。
"""

from __future__ import annotations

import re
import sqlite3
import sys

NEW_COLUMNS = (
    ("config_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("config_override", "JSON"),
    ("clock_shifted_at", "DATETIME"),
    ("clock_shift_assignments", "JSON"),
    ("retired_at", "DATETIME"),
)

#: 外键 CASCADE → RESTRICT 的表。重建按 sqlite_master 里的原 DDL 改写，
#: 索引原样随迁。
RESTRICT_TABLES = ("door_states", "door_state_events", "door_snapshots", "ota_release_installs")


def add_columns(conn: sqlite3.Connection) -> None:
    for name, ddl in NEW_COLUMNS:
        conn.execute(f"ALTER TABLE devices ADD COLUMN {name} {ddl}")


def move_data(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE devices SET"
        " config_revision = (SELECT COALESCE(MAX(revision), 0) FROM device_configs"
        "                     WHERE device_configs.device_id = devices.id),"
        " config_override = (SELECT config FROM device_configs WHERE device_configs.device_id = devices.id)"
    )
    conn.execute(
        "UPDATE devices SET"
        " clock_shifted_at = (SELECT shifted_at FROM device_clock_shifts WHERE device_clock_shifts.device_id = devices.id),"
        " clock_shift_assignments = (SELECT assignments FROM device_clock_shifts"
        "                            WHERE device_clock_shifts.device_id = devices.id)"
    )
    conn.execute("DROP TABLE device_configs")
    conn.execute("DROP TABLE device_clock_shifts")


def _rewrite_fk_ddl(ddl: str) -> str:
    """把建表 DDL 里 devices 外键的 ON DELETE CASCADE 改成 RESTRICT。"""
    pattern = re.compile(
        r'(CONSTRAINT\s+"?fk_' + r'(\w+)' r'_device_id_devices"?\s+FOREIGN KEY\s*\(\s*"?device_id"?\s*\)'
        r"\s+REFERENCES\s+devices\s*\(\s*\"?id\"?\s*\)\s*)ON DELETE CASCADE",
        re.IGNORECASE,
    )
    rewritten, count = pattern.subn(r"\1ON DELETE RESTRICT", ddl)
    return rewritten if count else ddl


def restrict_device_fks(conn: sqlite3.Connection, table: str) -> None:
    """重建一张表，把 devices 外键的 ON DELETE CASCADE 改为 RESTRICT。

    索引从 sqlite_master 取原文重建；没有 devices 外键的表按原样重建（防呆：
    调用清单写错时不静默跳过语义）。
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    if row is None:
        raise SystemExit(f"{table}: 库里没有这张表，清单与库不一致")
    (ddl,) = row
    new_ddl = _rewrite_fk_ddl(ddl)
    if new_ddl == ddl:
        raise SystemExit(f"{table}: 建表 DDL 里没有 devices 的 CASCADE 外键，清单与库不一致")
    indexes = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,)
    ).fetchall()
    tmp = f"{table}__wave1"
    create_tmp = re.sub(
        rf'CREATE TABLE\s+"?{re.escape(table)}\b"?', f'CREATE TABLE "{tmp}"', new_ddl, count=1, flags=re.IGNORECASE
    )
    conn.execute(create_tmp)
    conn.execute(f"INSERT INTO {tmp} SELECT * FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    for (index_sql,) in indexes:
        conn.execute(index_sql)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    conn = sqlite3.connect(sys.argv[1])
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        add_columns(conn)
        move_data(conn)
        for table in RESTRICT_TABLES:
            restrict_device_fks(conn, table)
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()
    print("wave1 迁移完成：devices 影子化 + 历史表外键 RESTRICT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
