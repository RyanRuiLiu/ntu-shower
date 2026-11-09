"""认证端点：会话建立、注销、当前账号。

会话建立只回答「账号口令对不对」，成功后把用户名写进 request.session（Cookie 的
签名、下发与注销由 SessionMiddleware 承担）；能力位与设备作用域仍由访问域守卫
判定，这里不复制那份逻辑。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.problems import UNAUTHENTICATED, ApiProblemError
from app.db.deps import get_db
from app.persistence import principals as principals_store

from .dependencies import current_user
from .schemas import AccountView, LoginRequest

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=AccountView)
def login(request: Request, body: LoginRequest, session: Session = Depends(get_db)) -> dict[str, Any]:
    """口令校验通过即签发会话。

    账号不存在与口令错误返回同一个问题类型，避免接口变成账号枚举器。
    """
    user = principals_store.authenticate(session, body.username, body.password)
    if user is None:
        raise ApiProblemError(UNAUTHENTICATED, "账号或口令不正确")
    request.session["u"] = user["username"]
    return {
        "username": user["username"],
        "capabilities": user["capabilities"],
        "device_scope": user["device_scope"],
    }


@router.post("/logout", status_code=204)
def logout(request: Request) -> None:
    """注销就是清空会话：无状态签名 Cookie 没有服务端记录可删，SessionMiddleware
    会把置空后的会话下发成一条过期 Cookie，浏览器随即丢弃它。
    """
    request.session.clear()


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
