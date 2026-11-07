"""认证端点：会话建立、注销、当前账号。

会话建立只回答「账号口令对不对」，成功后下发签名 Cookie；能力位与设备作用域仍由
访问域守卫判定，这里不复制那份逻辑。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.problems import UNAUTHENTICATED, ApiProblemError
from app.db.deps import get_db
from app.persistence import users as users_store

from . import sessions
from .dependencies import current_user
from .schemas import AccountView, LoginRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _cookie_secure() -> bool:
    """配了 https 对外基址才置 Secure；本地与测试走 http，Cookie 仍要能用。"""
    return settings.public_base_url.startswith("https://")


@router.post("/login", response_model=AccountView)
def login(body: LoginRequest, response: Response, session: Session = Depends(get_db)) -> dict[str, Any]:
    """口令校验通过即签发会话。

    账号不存在与口令错误返回同一个问题类型，避免接口变成账号枚举器。
    """
    user = users_store.authenticate(session, body.username, body.password)
    if user is None:
        raise ApiProblemError(UNAUTHENTICATED, "账号或口令不正确")
    token = sessions.issue(body.username)
    response.set_cookie(sessions.COOKIE_NAME, token, **sessions.cookie_options(_cookie_secure()))
    return {
        "username": user["username"],
        "capabilities": user["capabilities"],
        "device_scope": user["device_scope"],
    }


@router.post("/logout", status_code=204)
def logout(response: Response) -> Response:
    """清掉 Cookie。无状态会话没有服务端记录可删，注销就是让浏览器丢弃它。

    必须在这个注入的 Response 上删并返回同一个对象：另造一个 Response 会把
    Set-Cookie 头丢掉，浏览器里的旧会话就还在。
    """
    response.delete_cookie(sessions.COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=AccountView)
def me(user: dict[str, Any] | None = Depends(current_user)) -> dict[str, Any]:
    """当前会话对应的账号；无有效会话返回 401。"""
    if user is None:
        raise ApiProblemError(UNAUTHENTICATED, "需要有效会话")
    return {
        "username": user["username"],
        "capabilities": user["capabilities"],
        "device_scope": user["device_scope"],
    }


__all__: list[str] = ["router"]
