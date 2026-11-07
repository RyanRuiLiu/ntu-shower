"""过渡期适配层：旧签名 → 新持久化。

调用点还有几十处按旧签名（不带 session）调用，逐个改会把这次切换摊成几十个文件的
diff，且每一步都失去可比对的基线。这里保留旧签名，内部按当前配置建一个短生命周期
会话，转调 `app.persistence.access`——**只有一份真相**，旧名字只是入口。

全部调用点迁到 `app.persistence.access` 之后，本文件删除。
"""

from __future__ import annotations

from app.core.config import settings
from app.db.session import Database
from app.persistence import access as store

from .identity import IdentityKind
from .schemas import IdentityRecord


def init_tables() -> None:
    """表结构由 Alembic 建；保留空实现只为旧装配点的调用不变。"""


def put_identity(
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> IdentityRecord:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            return store.put_identity(session, identity, kind, capabilities, label, device_scope)
    finally:
        database.dispose()


def find_identity(identity: str) -> IdentityRecord | None:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            return store.find_identity(session, identity)
    finally:
        database.dispose()


def list_identities() -> list[IdentityRecord]:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            return store.list_identities(session)
    finally:
        database.dispose()


def delete_identity(identity: str) -> bool:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            return store.delete_identity(session, identity)
    finally:
        database.dispose()


__all__: list[str] = [
    "delete_identity",
    "find_identity",
    "init_tables",
    "list_identities",
    "put_identity",
]
