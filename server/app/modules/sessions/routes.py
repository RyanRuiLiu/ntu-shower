"""用户 API（v1）：分配 / 结算 / 拍照（匿名，鉴权对齐旧系统模型）。

cert_no/password 是浴室系统的业务凭证：随操作参数直传设备，
真正的凭证验证发生在浴室服务器端（密码错会在终端页被拒）。
本服务不做用户登录；未来的调用方身份体系（微信小程序 / 独立 APP）
按 docs/decisions/0034 的 provider 方案另行引入。
设备执行结果以结构化字段返回，terminal_message 为浴室 App 的人读文本。
分配/结算下发前依据 door_live 现态做预检，避免对占用门误触发结算流。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.problems import DEVICE_BUSY, DOOR_NOT_FREE, DUPLICATE_SESSION, NO_OCCUPIED_DOOR, ApiProblemError
from app.core.protocol import JsonObject
from app.core.security import require_admin
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import require_capability
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.doors import repository as doors_repository
from app.modules.doors import service as doors_service
from app.modules.sessions import repository
from app.modules.sessions.service import CommandOutcome

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["sessions-admin"])


class AssignRequest(BaseModel):
    device_id: str = Field(min_length=8)
    door_no: int = Field(ge=1)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SettleRequest(BaseModel):
    device_id: str = Field(min_length=8)
    cert_no: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PhotoRequest(BaseModel):
    device_id: str = Field(min_length=8)


def _door_state(device_id: str, door_no: int) -> str | None:
    row = doors_repository.get_door_live(device_id, door_no)
    return row["state"] if row is not None else None


def _reject_duplicate_session(device_id: str, cert_no: str) -> None:
    """同一学号已有活跃会话则拒绝（对齐旧系统 duplicate_session 语义）。"""
    active = doors_repository.get_active_by_cert(device_id, cert_no)
    if active is not None:
        raise ApiProblemError(
            DUPLICATE_SESSION,
            f"cert {cert_no} already active on door {active['door_no']}",
        )


def _reject_busy(result: CommandOutcome) -> CommandOutcome:
    """设备已有在途命令时拒绝本次请求（409），而非排队。"""
    if result.get("status") == "rejected":
        raise ApiProblemError(DEVICE_BUSY, f"reason={result.get('reason_code')}")
    return result


@router.post("/sessions/assign", dependencies=[Depends(require_capability(Capability.OPS))])
async def assign(req: AssignRequest) -> CommandOutcome:
    require_trusted_online_device(req.device_id)
    state = _door_state(req.device_id, req.door_no)
    if state != doors_service.STATE_FREE:
        raise ApiProblemError(DOOR_NOT_FREE, f"door {req.door_no} state={state}")
    _reject_duplicate_session(req.device_id, req.cert_no)
    result = await service.send_command_and_wait(
        req.device_id,
        "session.assign",
        {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.assign_timeout_s,
    )
    return _reject_busy(result)


@router.post("/sessions/settle", dependencies=[Depends(require_capability(Capability.OPS))])
async def settle(req: SettleRequest) -> CommandOutcome:
    require_trusted_online_device(req.device_id)
    occupied = doors_repository.get_occupied_by_cert(req.device_id, req.cert_no)
    if occupied is None:
        raise ApiProblemError(NO_OCCUPIED_DOOR, f"cert {req.cert_no}")
    result = await service.send_command_and_wait(
        req.device_id,
        "session.settle",
        {"cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.settle_timeout_s,
    )
    return _reject_busy(result)


@router.post("/photos", dependencies=[Depends(require_capability(Capability.PHOTO))])
async def take_photo(req: PhotoRequest) -> CommandOutcome:
    require_trusted_online_device(req.device_id)
    result = await service.send_command_and_wait(
        req.device_id,
        "photo.capture",
        {},
        timeout_s=settings.photo_timeout_s,
    )
    return _reject_busy(result)


@admin_router.get("/commands")
def list_commands(device_id: str | None = None, limit: int = 50) -> list[JsonObject]:
    """命令审计查询：调试与问题追踪。"""
    return repository.list_commands(device_id=device_id, limit=limit)
