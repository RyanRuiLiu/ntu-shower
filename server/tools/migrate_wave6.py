"""波次 6（auth 尾巴）一次性迁移：api_keys 表重建（跑一次验证后即删）。

两组变更（与 auth/models.py 最终形态对齐）：
① user_id 外键补 ON DELETE CASCADE——波次2 删除语义三态「凭据 CASCADE」，原先
   只有 ORM cascade，DB 层 NO ACTION，直连 SQL 删用户会撞外键；
② 补 ix_api_keys_user_id——删用户清子表与按用户列密钥都走它（SQLite 官方
   建议 FK 子列建索引）。

SQLite 不能原地改外键，整表按新形状重建：改名 → 建新 → 拷贝 → 删旧 → 回名。
用法：对生产库的**已停服副本**执行
    python tools/migrate_wave6.py <db_path>
单事务 + 防呆（形状不符即中止）+ 行数对账。
"""

from __future__ import annotations

import sqlite3
import sys

API_KEYS_NEW = """
CREATE TABLE api_keys_new (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name VARCHAR(64) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    scopes JSON NOT NULL,
    device_scope JSON NOT NULL,
    last_used_at DATETIME,
    created_at DATETIME NOT NULL,
    revoked_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    UNIQUE (token_hash)
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
    con.isolation_level = None
    con.execute("BEGIN")
    try:
        cols = _columns(con, "api_keys")
        _require(
            cols == ["id", "user_id", "name", "token_hash", "scopes", "device_scope", "last_used_at", "created_at", "revoked_at"],
            f"api_keys 列形状与预期不符：{cols}",
        )
        fk_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='api_keys'").fetchone()[0]
        _require("ON DELETE CASCADE" not in fk_sql, "api_keys 外键已带 CASCADE（已迁移过？）")

        count_before = con.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
        con.execute("DROP INDEX IF EXISTS ix_api_keys_user_id")
        con.execute("ALTER TABLE api_keys RENAME TO api_keys_old")
        con.execute(API_KEYS_NEW)
        con.execute(
            """
            INSERT INTO api_keys_new
                (id, user_id, name, token_hash, scopes, device_scope, last_used_at, created_at, revoked_at)
            SELECT id, user_id, name, token_hash, scopes, device_scope, last_used_at, created_at, revoked_at
            FROM api_keys_old
            """
        )
        con.execute("DROP TABLE api_keys_old")
        con.execute("ALTER TABLE api_keys_new RENAME TO api_keys")
        con.execute("CREATE INDEX ix_api_keys_user_id ON api_keys (user_id)")

        # 对账：行数一致、FK 带 CASCADE、索引在
        count_after = con.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
        _require(count_before == count_after, f"对账失败：行数 {count_before} → {count_after}")
        fk_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='api_keys'").fetchone()[0]
        _require("ON DELETE CASCADE" in fk_sql, "对账失败：CASCADE 未落")
        indexes = {row[1] for row in con.execute("PRAGMA index_list(api_keys)")}
        _require("ix_api_keys_user_id" in indexes, "对账失败：索引未建")

        con.execute("COMMIT")
        print(f"迁移完成：api_keys {count_after} 行完整重建（FK CASCADE + user_id 索引），事务已提交。")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python tools/migrate_wave6.py <db_path>", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1])
