"""影子 API：用户面 reported 视图与管理面完整文档。

用户面不额外包一层 `device_id`：路径里已有，`reported.device.device_id` 里也有，
第三份只是噪声。客户端拿到的 `reported` 与完整文档中的同名字段形状一致。
"""

from fastapi import APIRouter, Depends

from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.core.security import require_ops_token

from . import service
from .schemas import DeviceShadow, ShadowReported

router = APIRouter(prefix="/api/v1", tags=["shadow"])

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["shadow-ops"])


@router.get("/devices/{device_id}/shadow")
def get_device_shadow(device_id: str) -> ShadowReported:
    """用户面影子：reported 视图（设备档案 + 喷头现态）。"""
    reported = service.get_reported(device_id)
    if reported is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return reported


@ops_router.get("/devices/{device_id}/shadow")
def get_full_shadow(device_id: str) -> DeviceShadow:
    """管理面影子：reported + desired + metadata 完整文档。"""
    shadow = service.get_shadow(device_id)
    if shadow is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return shadow
