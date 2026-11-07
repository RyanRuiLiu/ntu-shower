"""测试用身份登记。

直接写新数据层，不经过任何过渡层：切换策略是一次性替换，没有兼容期。
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.db.session import Database
from app.modules.access.identity import IdentityKind
from app.persistence import principals as principals_store


def register(
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> str:
    """登记主体并签发一枚 Bearer 凭据，返回明文。"""
    database = Database(f"sqlite:///{Path(settings.db_path).as_posix()}")
    try:
        with database.new_session() as session:
            principals_store.put(session, identity, kind, capabilities, label, device_scope)
            token = principals_store.issue_token(session, identity)
    finally:
        database.dispose()
    assert token is not None
    return token


def bearer(token: str) -> dict[str, str]:
    """标准请求头；测试里不再出现自定的身份头。"""
    return {"Authorization": f"Bearer {token}"}
