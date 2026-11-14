"""测试用密钥签发。

直接写数据层，不经过任何过渡层：切换策略是一次性替换，没有兼容期。
"""

from __future__ import annotations

from pathlib import Path

from app.auth import crud
from app.core.config import settings
from app.db.session import Database


def register_key(
    username: str,
    scopes: list[str],
    device_scope: list[str] | None = None,
    name: str = "",
) -> str:
    """登记用户并签发一枚 Bearer 密钥，返回明文。"""
    database = Database(f"sqlite:///{Path(settings.db_path).as_posix()}")
    try:
        with database.new_session() as session:
            crud.create_user(session, username)
            issued = crud.issue_key(session, username, name, scopes, device_scope or ["*"])
    finally:
        database.dispose()
    assert issued is not None
    _, token = issued
    return token


def bearer(token: str) -> dict[str, str]:
    """标准请求头；测试里不再出现自定的身份头。"""
    return {"Authorization": f"Bearer {token}"}
