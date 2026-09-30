"""授权名单的持久化。

只有一张表：`access_grant` 回答"这个标识被授予了哪些能力"。

- **小程序用户**：`identity` 是微信 openid；
- **网页用户**：`identity` 是管理员签发的不透明标识。

两者都是"请求时附带的一个标识"，服务端查表得能力。不做会话、不做令牌过期——
标识本身长期有效，撤销靠从名单里删掉。
"""

import json
from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn

from .identity import IdentityKind
from .schemas import GrantRecord


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS access_grant (
            identity     TEXT PRIMARY KEY,
            kind         TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL
        );
        """
    )


def put_grant(identity: str, kind: IdentityKind, capabilities: list[str], label: str = "") -> None:
    """登记或更新一条授予（同一标识重复授予即更新其能力集）。

    刻意不返回记录：`created_at` 在冲突走 UPDATE 时保持原值，要拿到它就得回查，
    而调用方（管理员登记接口）需要的是"该身份现在有哪些能力"而非入库时间。
    名单视图请用 `list_grants`。
    """
    get_conn().execute(
        "INSERT INTO access_grant (identity, kind, capabilities, label, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(identity) DO UPDATE SET capabilities = excluded.capabilities, label = excluded.label",
        (identity, kind.value, json.dumps(capabilities), label, now_rfc3339()),
    )
    get_conn().commit()


def find_grant(identity: str) -> GrantRecord | None:
    """查一个标识的授权；不在名单里返回 None。"""
    row = get_conn().execute("SELECT * FROM access_grant WHERE identity = ?", (identity,)).fetchone()
    return _as_record(row) if row is not None else None


def list_grants() -> list[GrantRecord]:
    rows = get_conn().execute("SELECT * FROM access_grant ORDER BY created_at DESC").fetchall()
    return [_as_record(row) for row in rows]


def delete_grant(identity: str) -> bool:
    cursor = get_conn().execute("DELETE FROM access_grant WHERE identity = ?", (identity,))
    get_conn().commit()
    return cursor.rowcount > 0


def _as_record(row: Row) -> GrantRecord:
    """把行转成 GrantRecord；capabilities 以 JSON 文本入库，取出时还原为列表。"""
    record: GrantRecord = {
        "identity": row["identity"],
        "kind": row["kind"],
        "capabilities": json.loads(row["capabilities"]),
        "label": row["label"],
        "created_at": row["created_at"],
    }
    return record
