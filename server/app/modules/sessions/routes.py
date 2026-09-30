"""用户 API（v1）：分配 / 结算 / 拍照（匿名，鉴权对齐旧系统模型）。

cert_no/password 是浴室系统的业务凭证：随操作参数直传设备，
真正的凭证验证发生在浴室服务器端（密码错会在终端页被拒）。
本服务不做用户登录；未来的调用方身份体系（微信小程序 / 独立 APP）
按 docs/decisions/0034 的 provider 方案另行引入。
设备执行结果以结构化字段返回，terminal_message 为浴室 App 的人读文本。
分配/结算下发前依据 door_live 现态做预检，避免对占用门误触发结算流。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_admin
from app.modules.devices.routes import require_trusted_online_device
from app.modules.doors import repository as doors_repository
from app.modules.sessions import repository

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["sessions-admin"])

ASSIGN_TIMEOUT_S = 60.0
PHOTO_TIMEOUT_S = 15.0


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
        raise HTTPException(
            status_code=409,
            detail=f"cert {cert_no} already has an active session on door {active['door_no']}",
        )


def _reject_busy(result: dict) -> dict:
    """设备已有在途命令时拒绝本次请求（409），而非排队。"""
    if result.get("status") == "rejected":
        raise HTTPException(status_code=409, detail=f"device busy ({result.get('reason_code')})")
    return result


@router.post("/sessions/assign")
async def assign(req: AssignRequest) -> dict:
    require_trusted_online_device(req.device_id)
    state = _door_state(req.device_id, req.door_no)
    if state != "free":
        raise HTTPException(status_code=409, detail=f"door not free (state={state})")
    _reject_duplicate_session(req.device_id, req.cert_no)
    result = await service.send_command_and_wait(
        req.device_id,
        "session.assign",
        {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
        timeout_s=ASSIGN_TIMEOUT_S,
    )
    return _reject_busy(result)


@router.post("/sessions/settle")
async def settle(req: SettleRequest) -> dict:
    require_trusted_online_device(req.device_id)
    occupied = doors_repository.get_occupied_by_cert(req.device_id, req.cert_no)
    if occupied is None:
        raise HTTPException(status_code=409, detail="no occupied door for this account")
    result = await service.send_command_and_wait(
        req.device_id,
        "session.settle",
        {"cert_no": req.cert_no, "password": req.password},
        timeout_s=ASSIGN_TIMEOUT_S,
    )
    return _reject_busy(result)


@router.post("/photos")
async def take_photo(req: PhotoRequest) -> dict:
    require_trusted_online_device(req.device_id)
    result = await service.send_command_and_wait(
        req.device_id,
        "photo.capture",
        {},
        timeout_s=PHOTO_TIMEOUT_S,
    )
    return _reject_busy(result)


@admin_router.get("/commands")
def list_commands(device_id: str | None = None, limit: int = 50) -> list[dict]:
    """命令审计查询：调试与问题追踪。"""
    return repository.list_commands(device_id=device_id, limit=limit)
