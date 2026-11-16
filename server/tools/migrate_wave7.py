"""波次 7 一次性迁移：voice_deliveries.segments → summary（跑一次验证后即删）。

投递摘要列的旧名 segments 与规则段/协议段的 segments 同名三义（列里存的是
人类可读摘要文本，不是播放清单）——列名改 summary 消除歧义。SQLite 直接
RENAME COLUMN 即可，无需重建表。

用法：python tools/migrate_wave7.py <db_path>
"""

from __future__ import annotations

import sqlite3
import sys


def migrate(path: str) -> None:
    con = sqlite3.connect(path)
    con.isolation_level = None
    con.execute("BEGIN")
    try:
        cols = [row[1] for row in con.execute("PRAGMA table_info(voice_deliveries)")]
        if "summary" in cols and "segments" not in cols:
            print("已迁移过（summary 在、segments 不在）：提交空事务。")
            con.execute("COMMIT")
            return
        assert "segments" in cols, f"voice_deliveries 列形状与预期不符：{cols}"
        con.execute("ALTER TABLE voice_deliveries RENAME COLUMN segments TO summary")
        check = [row[1] for row in con.execute("PRAGMA table_info(voice_deliveries)")]
        assert "summary" in check and "segments" not in check, "对账失败"
        con.execute("COMMIT")
        print("迁移完成：voice_deliveries.segments → summary，事务已提交。")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python tools/migrate_wave7.py <db_path>", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1])
