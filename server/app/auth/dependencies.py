"""会话依赖：把 Cookie 解析成当前账号。

与 `modules/access/dependencies.py` 的分工：这里只负责「是不是一个已认证账号」，
能力位与设备作用域的判定仍走访问域那一条唯一实现，不另造一套判据。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.persistence import users as users_store

from . import sessions


def current_user(request: Request, session: Session = Depends(get_db)) -> dict[str, Any] | None:
    """当前已认证账号；无有效会话、签名失效、账号被禁用都返回 None。"""
    username = sessions.verify(request.cookies.get(sessions.COOKIE_NAME))
    if username is None:
        return None
    user = users_store.find_user(session, username)
    if user is None or user["disabled"]:
        return None
    return user


__all__: list[str] = ["current_user"]
