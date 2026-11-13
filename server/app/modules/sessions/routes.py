"""会话域 API：分配、结算、拍照（POST /devices/{device_id}/<动词>）。

目标设备一律由路径给出，请求体只带业务参数。cert_no/password 是浴室系统的
业务凭证，随操作直传设备，真正的凭证验证发生在浴室服务器端。

领域逻辑（预检、下发、过载拒绝）在 service.perform_*：scheduler 与 extend_hours
复用同一条路径。这里只做 HTTP 的事：鉴权、能力位、把领域异常转给错误处理器。

守卫：操作必须带身份（require_identity），函数体内 enforce(caller, 能力位,
device_id)；能力位为 session（拍照为 photo），设备作用域按身份判定。管理台凭
admin 身份持有的 session/photo 能力位调用同一路径（见 access.dependencies）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.contracts import CommandOutcome
from app.db.deps import get_db
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import enforce, require_identity
from app.modules.access.schemas import IdentityRecord
from app.modules.sessions.schemas import AssignRequest, SettleRequest

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])


@router.post("/devices/{device_id}/assign")
async def assign(
    device_id: str,
    req: AssignRequest,
    caller: IdentityRecord = Depends(require_identity),
    session: Session = Depends(get_db),
) -> CommandOutcome:
    """分配喷头。要求 session 能力位且设备在作用域内。"""
    enforce(caller, Capability.SESSION, device_id)
    return await service.perform_assign(session, device_id, req)


@router.post("/devices/{device_id}/settle")
async def settle(
    device_id: str,
    req: SettleRequest,
    caller: IdentityRecord = Depends(require_identity),
    session: Session = Depends(get_db),
) -> CommandOutcome:
    """结算。要求 session 能力位且设备在作用域内。"""
    enforce(caller, Capability.SESSION, device_id)
    return await service.perform_settle(session, device_id, req)


@router.post("/devices/{device_id}/photo")
async def photo(
    device_id: str, caller: IdentityRecord = Depends(require_identity), session: Session = Depends(get_db)
) -> CommandOutcome:
    """摄像头拍照。要求 photo 能力位且设备在作用域内。"""
    enforce(caller, Capability.PHOTO, device_id)
    return await service.perform_photo(session, device_id)
