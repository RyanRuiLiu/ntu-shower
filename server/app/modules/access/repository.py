"""授权名单的持久化。

只有一张表：`access_grant` 回答"这个标识被授予了哪些能力"。

- **小程序用户**：`identity` 是微信 openid；
- **网页用户**：`identity` 是管理员生成的不透明标识。

两者都是"请求时附带的一个标识"，服务端查表得能力。不做会话、不做令牌过期——
标识本身长期有效，撤销靠从名单里删掉（`kind` 让管理员能分辨谁是小程序用户、
谁是网页标识）。
"""

import json

from app.core.clock import now_rfc3339
from app.core.db import get_conn
from app.core.protocol import JsonObject

#: 标识类型。
KIND_OPENID = "openid"
KIND_TOKEN = "token"


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


def put_grant(identity: str, kind: str, capabilities: list[str], label: str = "") -> None:
    """登记或更新一条授予（同一标识重复授予即更新其能力集）。"""
    get_conn().execute(
        "INSERT INTO access_grant (identity, kind, capabilities, label, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(identity) DO UPDATE SET capabilities = excluded.capabilities, label = excluded.label",
        (identity, kind, json.dumps(capabilities), label, now_rfc3339()),
    )
    get_conn().commit()


def find_grant(identity: str) -> JsonObject | None:
    """查一个标识的授权；不在名单里返回 None。"""
    row = get_conn().execute("SELECT * FROM access_grant WHERE identity = ?", (identity,)).fetchone()
    if row is None:
        return None
    found = dict(row)
    found["capabilities"] = json.loads(found["capabilities"])
    return found


def list_grants() -> list[JsonObject]:
    rows = get_conn().execute("SELECT * FROM access_grant ORDER BY created_at DESC").fetchall()
    return [{**dict(row), "capabilities": json.loads(row["capabilities"])} for row in rows]


def delete_grant(identity: str) -> bool:
    cursor = get_conn().execute("DELETE FROM access_grant WHERE identity = ?", (identity,))
    get_conn().commit()
    return cursor.rowcount > 0
