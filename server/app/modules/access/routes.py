"""访问授权 API：小程序登录、查询自身能力、管理员维护授权名单。

守卫分工：用户面用 `X-Identity` 标识（能力判定），管理面用运维令牌
（`X-Admin-Token`）。授权名单的改动只应来自运维通道。
"""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.problems import LOGIN_REJECTED, UNKNOWN_CAPABILITY, ApiProblemError
from app.core.protocol import JsonObject
from app.core.security import require_admin

from . import service
from .capabilities import ALL_CAPABILITIES
from .dependencies import require_identity

router = APIRouter(prefix="/api/v1", tags=["access"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["access-admin"])


class LoginRequest(BaseModel):
    """小程序登录：wx.login 拿到的 code。"""

    code: str = Field(min_length=1, description="wx.login 返回的临时凭证")


class GrantRequest(BaseModel):
    """管理员登记一个小程序用户的授权。"""

    openid: str = Field(min_length=8, description="该用户的微信 openid")
    capabilities: list[str] = Field(min_length=1)
    label: str = Field(default="", description="人读备注（是谁）")


class WebIdentityRequest(BaseModel):
    """管理员为网页端签发标识。"""

    label: str = Field(min_length=1, description="人读备注（给谁用的）")
    capabilities: list[str] = Field(min_length=1)


@router.post("/session")
def login(req: LoginRequest) -> JsonObject:
    """小程序登录：用 wx.login 的 code 换取身份标识。

    返回 openid 本身（客户端存下、此后每次请求带上）与已授予的能力。
    查看类接口不需要登录，只有操作类接口才需要。
    """
    issued = service.login_with_wx_code(req.code)
    if issued is None:
        raise ApiProblemError(LOGIN_REJECTED, "凭证无效或未被授权")
    return issued


@router.get("/me")
def get_my_capabilities(caller: JsonObject = Depends(require_identity)) -> JsonObject:
    """当前标识对应的能力。

    返回**全部能力**而非"本次校验的那一个"：前端据此决定显示或隐藏按钮，
    一次问清比按能力逐个试探更省往返。
    """
    return {"capabilities": caller["capabilities"]}


@admin_router.get("/grants")
def list_grants() -> list[JsonObject]:
    """授权名单。

    只列标识与能力；网页标识交付后就只留备注，不在此回显（名单是管理视图，
    不是凭据分发处）。
    """
    return service.list_grants()


@admin_router.post("/grants")
def put_grant(req: GrantRequest) -> JsonObject:
    """登记或更新一个小程序用户的授权。"""
    _reject_unknown_capabilities(req.capabilities)
    return service.authorize_openid(req.openid, req.capabilities, req.label)


@admin_router.delete("/grants/{identity}", status_code=204)
def delete_grant(identity: str) -> Response:
    """撤销授权；该标识立即失去所有能力。"""
    service.revoke_identity(identity)
    return Response(status_code=204)


@admin_router.post("/grants/web-identity")
def create_web_identity(req: WebIdentityRequest) -> JsonObject:
    """为网页端签发标识：网页拿不到 openid，标识本身就是它的身份。

    标识**只在本次响应里返回**，请当场交付给使用者。
    """
    _reject_unknown_capabilities(req.capabilities)
    return service.issue_web_identity(req.label, req.capabilities)


def _reject_unknown_capabilities(capabilities: list[str]) -> None:
    unknown = sorted(set(capabilities) - ALL_CAPABILITIES)
    if unknown:
        raise ApiProblemError(UNKNOWN_CAPABILITY, f"未知能力：{unknown}")
