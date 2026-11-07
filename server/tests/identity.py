"""测试用身份登记。

直接写新数据层，不经过任何过渡层：切换策略是一次性替换，没有兼容期。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.db.session import Database
from app.modules.access.identity import IdentityKind
from app.persistence import access as access_store


def register(
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> None:
    database = Database(f"sqlite:///{Path(settings.db_path).as_posix()}")
    try:
        with database.new_session() as session:
            access_store.put_identity(session, identity, kind, capabilities, label, device_scope)
    finally:
        database.dispose()
