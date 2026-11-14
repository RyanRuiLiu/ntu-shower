"""会话域 API：分配、结算、拍照（POST /devices/{device_id}/<动词>）。

目标设备一律由路径给出，请求体只带业务参数。cert_no/password 是浴室系统的
业务凭证，随操作直传设备，真正的凭证验证发生在浴室服务器端。

领域逻辑（预检、下发、过载拒绝）在 service.perform_*：scheduler 与 extend_hours
复用同一条路径。这里只做 HTTP 的事：把领域异常转给错误处理器。

守卫：`sessions:write`（拍照为 `devices:photo`）+ 对象级 device_scope 检查
（auth.dependencies），外部业务系统凭最小权限密钥即可调用（RFC 6749 scope 语义）。
"""

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard
from app.contracts import CommandOutcome
from app.db.deps import get_db
from app.modules.sessions.schemas import AssignRequest, SettleRequest

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])


@router.post(
    "/devices/{device_id}/assign",
    dependencies=[Security(caller, scopes=["sessions:write"]), Depends(device_scope_guard)],
)
async def assign(
    device_id: str,
    req: AssignRequest,
    session: Session = Depends(get_db),
) -> CommandOutcome:
    """分配喷头。要求 sessions:write 且设备在密钥作用域内。"""
    return await service.perform_assign(session, device_id, req)


@router.post(
    "/devices/{device_id}/settle",
    dependencies=[Security(caller, scopes=["sessions:write"]), Depends(device_scope_guard)],
)
async def settle(
    device_id: str,
    req: SettleRequest,
    session: Session = Depends(get_db),
) -> CommandOutcome:
    """结算。要求 sessions:write 且设备在密钥作用域内。"""
    return await service.perform_settle(session, device_id, req)


@router.post(
    "/devices/{device_id}/photo",
    dependencies=[Security(caller, scopes=["devices:photo"]), Depends(device_scope_guard)],
)
async def photo(
    device_id: str,
    session: Session = Depends(get_db),
) -> CommandOutcome:
    """摄像头拍照。要求 devices:photo 且设备在密钥作用域内。"""
    return await service.perform_photo(session, device_id)
