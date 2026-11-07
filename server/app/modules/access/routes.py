"""身份域 API：名单维护与身份签发。

## 资源与形状

`GET /identities` 返回身份域的完整读模型：能力位枚举（登记表派生，身份页表单
的唯一来源）与名单本身，一次装载。登记（POST）与编辑（PUT）都返回 IdentityRecord；
撤销（DELETE）即失权。

## 守卫

整个域要求 admin 能力位（X-Identity 头，统一身份制见 access.dependencies）。

## 写入校验

能力名必须是登记表里的；作用域里的设备必须真实存在——写错的授权永远执行不了，
挡在门口。
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.problems import UNKNOWN_CAPABILITY, VALIDATION_FAILED, ApiProblemError
from app.db.deps import get_db

from . import service
from .capabilities import ALL_CAPABILITIES, Capability, cap_label
from .dependencies import require_admin, require_identity
from .identity import IdentityKind
from .schemas import IdentityCatalog, IdentityRecord, IdentityRequest

#: 身份域里"我自己"那一面。要排在那条带 require_admin 的 router 之前注册（见 app/main.py）。
router = APIRouter(prefix="/api/v1/identities", tags=["identities"])

admin_router = APIRouter(prefix="/api/v1/identities", dependencies=[Depends(require_admin)], tags=["identities"])


@router.get("/me")
def get_own_identity(caller: IdentityRecord = Depends(require_identity)) -> IdentityRecord:
    """自己的身份记录：标识、能力位、设备作用域。

    客户端要判断"我有没有某个能力位"，不带上它就只能拿"调那个接口收 403"反推——而那是
    一次**常态查询**，用错误码回答会往审计与日志里灌一串本可避免的 403。这里一次 200
    说完，顺带也是名单的对照面：名单是管理员看的全部，这里是我自己那一条。

    路径不是 `/identity`：那个单数顶层路径曾作为独立资源存在过，已被
    `tests/test_access.py` 钉成 404——统一资源树的取向是 URL 只表达资源。
    """
    return caller


@admin_router.get("")
def list_identities(session: Session = Depends(get_db)) -> IdentityCatalog:
    """身份域读模型：能力位枚举 + 名单，身份页一次装载。"""
    return {
        "capabilities": [{"key": cap.value, "label": cap_label(cap)} for cap in Capability],
        "identities": service.list_identities(session),
    }


@admin_router.post("")
def put_identity(req: IdentityRequest, session: Session = Depends(get_db)) -> IdentityRecord:
    """登记一个标识。

    - `kind=openid`：登记一个小程序用户（`openid` 必填，重复登记即整体更新）
    - `kind=web`：签发一个网页标识——`identity` 留空由服务端生成（明文只在本次
      响应返回），给了值即为自创标识

    两种来源共用一个端点：它们创建的是同一种资源。
    """
    _reject_unknown_capabilities(req.capabilities)
    if req.kind is IdentityKind.OPENID and not req.openid:
        raise ApiProblemError(VALIDATION_FAILED, "kind=openid 时必须提供 openid")
    if req.kind is IdentityKind.WEB and req.identity is not None and service.resolve_identity(session, req.identity):
        # 自创串撞上既有标识会静默覆盖它的能力位，必须显式走 PUT 编辑
        raise ApiProblemError(VALIDATION_FAILED, "该标识已存在，编辑请用 PUT")
    _reject_unknown_devices(session, req.device_scope)
    desired = req.openid if req.kind is IdentityKind.OPENID else (req.identity or "")
    return service.put_identity(session, req.kind, req.capabilities, req.label, desired, req.device_scope)


@admin_router.put("/{identity}")
def update_identity(identity: str, req: IdentityRequest, session: Session = Depends(get_db)) -> IdentityRecord:
    """编辑一个既有标识：能力位、设备作用域与备注。标识本身不变，下一次请求即生效。"""
    _reject_unknown_capabilities(req.capabilities)
    # 标识由路径给出：编辑不改身份本身，body 里的 openid 字段忽略
    _reject_unknown_devices(session, req.device_scope)
    return service.update_identity(session, identity, req.kind, req.capabilities, req.label, req.device_scope)


@admin_router.delete("/{identity}", status_code=204)
def delete_identity(identity: str, session: Session = Depends(get_db)) -> Response:
    """撤销授权；该标识立即失去所有能力。

    成功即 204、无响应体：撤销没有可供回报的领域状态。与发布下架（DELETE
    /api/v1/ota/releases/{version}，返回 ReleaseRemoved）不同——那边要回报注册行与
    APK 文件是否真的删了。
    """
    service.revoke_identity(session, identity)
    return Response(status_code=204)


def _reject_unknown_devices(_session: Session, device_scope: list[str]) -> None:
    """作用域里的设备必须真实存在：写错 device_id 的身份永远执行不了。

    过渡期读旧设备表：设备域尚未迁到新数据层，两边同时读会在切换中途出现
    「设备存在但校验说没有」。设备域迁完后这里一并改到 `persistence.devices`。
    """
    from app.modules.devices import repository as devices_repository

    known = {record["device_id"] for record in devices_repository.list_all()}
    unknown = sorted(set(device_scope) - known - {"*"})
    if unknown:
        raise ApiProblemError(VALIDATION_FAILED, f"作用域含未知设备：{unknown}")


def _reject_unknown_capabilities(capabilities: list[str]) -> None:
    unknown = sorted(set(capabilities) - ALL_CAPABILITIES)
    if unknown:
        raise ApiProblemError(UNKNOWN_CAPABILITY, f"未知能力：{unknown}")
