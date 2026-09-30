"""访问授权 API：换取标识、登记标识、维护名单。

## 一个资源

名单里的东西是**标识**（identity）——微信给的 openid 和我方签发的网页标识
是同一种资源，只是 `kind` 不同。因此：

- 用户面：`/api/v1/identity`（换取、查询自己）
- 管理面：`/ops/identities`（名单、登记、签发、撤销）

此前是 `/api/v1/session` + `/api/v1/me` + `/ops/grants/*`——同一种资源三个
名字，且 `session` 与淋浴会话（`/api/v1/sessions/*`）撞词。

## 守卫分工

用户面用 `X-Identity` 标识（能力判定），管理面用运维令牌（`X-Ops-Token`）。
名单的改动只应来自运维通道。
"""

from fastapi import APIRouter, Depends, Response

from app.core.problems import LOGIN_REJECTED, UNKNOWN_CAPABILITY, VALIDATION_FAILED, ApiProblemError
from app.core.security import require_ops_token

from . import service
from .capabilities import ALL_CAPABILITIES
from .dependencies import require_identity
from .identity import IdentityKind
from .schemas import IdentityRecord, IdentityRequest, WxLoginRequest

router = APIRouter(prefix="/api/v1", tags=["access"])

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["access-ops"])


@router.post("/identity")
def login(req: WxLoginRequest) -> IdentityRecord:
    """小程序换取标识：用 wx.login 的 code 换 openid。

    查看类接口不需要登录，只有操作类接口才需要。
    """
    record = service.login_with_wx_code(req.code)
    if record is None:
        raise ApiProblemError(LOGIN_REJECTED, "凭证无效或未被授权")
    return record


@router.get("/identity")
def get_my_identity(caller: IdentityRecord = Depends(require_identity)) -> IdentityRecord:
    """当前标识对应的记录。

    返回**全部能力**而非"本次校验的那一个"：客户端据此决定显示或隐藏按钮，
    一次问清比按能力逐个试探更省往返。
    """
    return caller


@ops_router.get("/identities")
def list_identities() -> list[IdentityRecord]:
    """名单。"""
    return service.list_identities()


@ops_router.post("/identities")
def put_identity(req: IdentityRequest) -> IdentityRecord:
    """登记一个标识。

    - `kind=openid`：登记一个小程序用户（`openid` 必填）
    - `kind=web`：签发一个网页标识（`openid` 留空，服务端生成并**只在本次响应返回**）

    两种来源共用一个端点：它们创建的是同一种资源。
    """
    _reject_unknown_capabilities(req.capabilities)
    if req.kind is IdentityKind.OPENID and not req.openid:
        # 请求格式问题，不是能力名问题——用 validation-failed 才对得上语义
        raise ApiProblemError(VALIDATION_FAILED, "kind=openid 时必须提供 openid")
    return service.put_identity(req.kind, req.capabilities, req.label, req.openid)


@ops_router.delete("/identities/{identity}", status_code=204)
def delete_identity(identity: str) -> Response:
    """撤销授权；该标识立即失去所有能力。"""
    service.revoke_identity(identity)
    return Response(status_code=204)


def _reject_unknown_capabilities(capabilities: list[str]) -> None:
    unknown = sorted(set(capabilities) - ALL_CAPABILITIES)
    if unknown:
        raise ApiProblemError(UNKNOWN_CAPABILITY, f"未知能力：{unknown}")
