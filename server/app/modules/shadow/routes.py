"""影子 API：用户面 reported 视图与管理面完整文档。"""

from fastapi import APIRouter, Depends

from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.core.protocol import JsonObject
from app.core.security import require_admin

from . import service

router = APIRouter(prefix="/api/v1", tags=["shadow"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["shadow-admin"])


@router.get("/devices/{device_id}/shadow")
def get_device_shadow(device_id: str) -> JsonObject:
    """用户面影子：reported 视图（门状态 + 设备在线/名称）。"""
    reported = service.get_reported(device_id)
    if reported is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, device_id)
    return {"device_id": device_id, "reported": reported}


@admin_router.get("/devices/{device_id}/shadow")
def get_full_shadow(device_id: str) -> JsonObject:
    """管理面影子：reported + desired + metadata 完整文档。"""
    shadow = service.get_shadow(device_id)
    if shadow is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, device_id)
    return shadow
