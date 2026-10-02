"""会话域 API：分配、结算、拍照（POST /devices/{device_id}/<动词>）。

目标设备一律由路径给出，请求体只带业务参数。cert_no/password 是浴室系统的
业务凭证，随操作直传设备，真正的凭证验证发生在浴室服务器端。分配与结算下发
前依据 door_live 现态预检，避免对占用门误触发结算流。

守卫：操作必须带身份（require_identity），函数体内 enforce(caller, 能力位,
device_id)；能力位为 session（拍照为 photo），设备作用域按身份判定。管理台凭
admin 身份持有的 session/photo 能力位调用同一路径（见 access.dependencies）。
"""

from fastapi import APIRouter, Depends

from app.core import actions, outcomes
from app.core.config import settings
from app.core.problems import (
    DOOR_NOT_FOUND,
    DOOR_NOT_FREE,
    DUPLICATE_SESSION,
    NO_OCCUPIED_DOOR,
    SERVER_OVERLOADED,
    ApiProblemError,
)
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import enforce, require_identity
from app.modules.access.schemas import IdentityRecord
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.doors import repository as doors_repository
from app.modules.doors.schemas import ASSIGNABLE_STATES, DoorState
from app.modules.sessions.schemas import AssignRequest, CommandOutcome, SettleRequest

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])


def _require_assignable_door(device_id: str, door_no: int) -> None:
    """分配前的喷头现态预检。

    两种失败分开报：喷头从未上报过是 404（编号不存在），状态不可分配是 409。
    可分配状态以 doors.schemas.ASSIGNABLE_STATES 为唯一判定，这里只负责转成
    HTTP 错误；喷头编号不硬编码——存在哪些喷头是设备上报的事实。
    """
    row = doors_repository.get_door_live(device_id, door_no)
    if row is None:
        raise ApiProblemError(DOOR_NOT_FOUND, f"喷头 {door_no} 从未被上报")
    raw_state = row["state"]
    try:
        state: DoorState | None = DoorState(raw_state)
    except ValueError:
        # 设备上报了本端不认识的状态：不分配，也不去猜它是什么意思
        state = None
    if state not in ASSIGNABLE_STATES:
        raise ApiProblemError(DOOR_NOT_FREE, f"喷头 {door_no} 当前状态：{raw_state}")


def _reject_duplicate_session(device_id: str, cert_no: str) -> None:
    """同一学号已有活跃会话则拒绝。"""
    active = doors_repository.get_active_by_cert(device_id, cert_no)
    if active is not None:
        raise ApiProblemError(
            DUPLICATE_SESSION,
            f"学号 {cert_no} 已在喷头 {active['door_no']} 有进行中的会话",
        )


def _reject_overload(result: CommandOutcome) -> CommandOutcome:
    """通道排队满时把拒绝转成 409。

    这不是"设备忙"：同通道的先后命令会排队执行，只有队满才拒。前者说明设备正
    忙、等一会就好；后者说明积压已多、该换个时间再来。
    """
    if result.get("status") == outcomes.REJECTED.value:
        raise ApiProblemError(SERVER_OVERLOADED, f"原因码：{result.get('reason_code')}")
    return result


async def perform_assign(device_id: str, req: AssignRequest) -> CommandOutcome:
    """分配喷头的领域逻辑，所有调用方共用。"""
    require_trusted_online_device(device_id)
    _require_assignable_door(device_id, req.door_no)
    _reject_duplicate_session(device_id, req.cert_no)
    result = await service.send_command_and_wait(
        device_id,
        actions.SESSION_ASSIGN,
        {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.assign_timeout_s,
    )
    return _reject_overload(result)


async def perform_settle(device_id: str, req: SettleRequest) -> CommandOutcome:
    """结算的领域逻辑。"""
    require_trusted_online_device(device_id)
    occupied = doors_repository.get_occupied_by_cert(device_id, req.cert_no)
    if occupied is None:
        raise ApiProblemError(NO_OCCUPIED_DOOR, f"学号 {req.cert_no} 没有正在使用的喷头")
    result = await service.send_command_and_wait(
        device_id,
        actions.SESSION_SETTLE,
        {"cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.settle_timeout_s,
    )
    return _reject_overload(result)


async def perform_photo(device_id: str) -> CommandOutcome:
    """拍照的领域逻辑。"""
    require_trusted_online_device(device_id)
    result = await service.send_command_and_wait(
        device_id,
        actions.CAMERA_CAPTURE,
        {},
        timeout_s=settings.photo_timeout_s,
    )
    return _reject_overload(result)


@router.post("/devices/{device_id}/assign")
async def assign(
    device_id: str,
    req: AssignRequest,
    caller: IdentityRecord = Depends(require_identity),
) -> CommandOutcome:
    """分配喷头。要求 session 能力位且设备在作用域内。"""
    enforce(caller, Capability.SESSION, device_id)
    return await perform_assign(device_id, req)


@router.post("/devices/{device_id}/settle")
async def settle(
    device_id: str,
    req: SettleRequest,
    caller: IdentityRecord = Depends(require_identity),
) -> CommandOutcome:
    """结算。要求 session 能力位且设备在作用域内。"""
    enforce(caller, Capability.SESSION, device_id)
    return await perform_settle(device_id, req)


@router.post("/devices/{device_id}/photo")
async def photo(device_id: str, caller: IdentityRecord = Depends(require_identity)) -> CommandOutcome:
    """摄像头拍照。要求 photo 能力位且设备在作用域内。"""
    enforce(caller, Capability.PHOTO, device_id)
    return await perform_photo(device_id)
