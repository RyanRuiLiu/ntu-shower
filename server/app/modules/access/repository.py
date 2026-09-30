"""标识名单的持久化。

只有一张表：`access_identity` 回答"这个标识被授予了哪些能力"。

- **小程序用户**：`identity` 是微信 openid；
- **网页用户**：`identity` 是我方签发的不透明标识。

两者都是"请求时附带的一个标识"，服务端查表得能力。不做会话、不做令牌过期——
标识本身长期有效，撤销靠从名单里删掉。
"""

import json
from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn

from .identity import IdentityKind
from .schemas import IdentityRecord


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS access_identity (
            identity     TEXT PRIMARY KEY,
            kind         TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL
        );
        """
    )


def put_identity(
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
) -> IdentityRecord:
    """登记或更新一条标识（同一标识重复登记即更新其能力集）。

    返回登记后的完整记录。`created_at` 在冲突走 UPDATE 时保持原值，因此回查一次
    取值——调用方拿到的是与名单视图完全一致的形状，不必再自己拼一个子集模型。
    """
    get_conn().execute(
        "INSERT INTO access_identity (identity, kind, capabilities, label, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(identity) DO UPDATE SET capabilities = excluded.capabilities, label = excluded.label",
        (identity, kind.value, json.dumps(capabilities), label, now_rfc3339()),
    )
    get_conn().commit()
    record = find_identity(identity)
    if record is None:
        # 刚写入就查不到只可能是数据库层面的异常（并发删除）。mypy 需要这里的
        # 收窄：不返回字段残缺的对象。
        message = "identity vanished right after write"
        raise RuntimeError(message)
    return record


def find_identity(identity: str) -> IdentityRecord | None:
    """查一个标识；不在名单里返回 None。"""
    row = get_conn().execute("SELECT * FROM access_identity WHERE identity = ?", (identity,)).fetchone()
    return _as_identity(row) if row is not None else None


def list_identities() -> list[IdentityRecord]:
    rows = get_conn().execute("SELECT * FROM access_identity ORDER BY created_at DESC").fetchall()
    return [_as_identity(row) for row in rows]


def delete_identity(identity: str) -> bool:
    cursor = get_conn().execute("DELETE FROM access_identity WHERE identity = ?", (identity,))
    get_conn().commit()
    return cursor.rowcount > 0


def _as_identity(row: Row) -> IdentityRecord:
    """把行转成 IdentityRecord。

    `capabilities` 以 JSON 文本入库，取出时还原为列表；`kind` 还原为枚举，
    让它在 OpenAPI 与调用方那里都是受约束的取值而非裸字符串。
    """
    record: IdentityRecord = {
        "identity": row["identity"],
        "kind": IdentityKind(row["kind"]),
        "capabilities": json.loads(row["capabilities"]),
        "label": row["label"],
        "created_at": row["created_at"],
    }
    return record
