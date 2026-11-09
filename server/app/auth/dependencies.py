"""会话依赖：把 request.session 解析成当前账号。

与 `modules/access/dependencies.py` 的分工：这里只负责「是不是一个已认证账号」，
能力位与设备作用域的判定仍走访问域那一条唯一实现，不另造一套判据。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.persistence import principals as principals_store


def current_user(request: Request, session: Session = Depends(get_db)) -> dict[str, Any] | None:
    """当前已认证账号；无有效会话、签名失效、账号被禁用都返回 None。

    签名与过期由 SessionMiddleware（itsdangerous）在进到端点之前验完——签名不对或
    超龄的 Cookie 到这里已是一个空会话。能力位与禁用位仍每次查库：禁用账号立即
    失效，不等会话过期。
    """
    username = request.session.get("u")
    if not isinstance(username, str) or not username:
        return None
    user = principals_store.find_account(session, username)
    if user is None or user["disabled"]:
        return None
    return user


__all__: list[str] = ["current_user"]
