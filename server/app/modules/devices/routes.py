"""设备 API：管理面（注册表管理）与用户面依赖守卫。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import require_admin

from . import service

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["devices-admin"])


class DeviceNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class DeviceExecRequest(BaseModel):
    command: str = Field(min_length=1)


@admin_router.get("/devices")
def list_devices() -> list[dict]:
    return service.list_devices()


@admin_router.post("/devices/{device_id}/trust")
def trust_device(device_id: str) -> dict:
    found = service.set_trusted(device_id, True)
    return {"device_id": device_id, "trusted": True, "found": found}


@admin_router.post("/devices/{device_id}/untrust")
def untrust_device(device_id: str) -> dict:
    found = service.set_trusted(device_id, False)
    return {"device_id": device_id, "trusted": False, "found": found}


@admin_router.patch("/devices/{device_id}")
def rename_device(device_id: str, req: DeviceNameRequest) -> dict:
    found = service.rename_device(device_id, req.name)
    return {"device_id": device_id, "name": req.name, "found": found}


@admin_router.post("/devices/{device_id}/exec")
async def exec_command(device_id: str, req: DeviceExecRequest) -> dict:
    """远程调试：在指定设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    require_trusted_online_device(device_id)
    from app.modules.sessions import service as sessions_service

    return await sessions_service.send_command_and_wait(
        device_id,
        "debug.exec",
        {"command": req.command},
        timeout_s=30.0,
    )


def require_trusted_online_device(device_id: str) -> dict:
    """依赖守卫：设备必须存在且已信任、当前在线；否则拒绝进入业务。"""
    device = service.get_device(device_id)
    if device is None or not device["trusted"]:
        raise HTTPException(status_code=404, detail="device not found")
    if not service.is_online(device_id):
        raise HTTPException(status_code=409, detail="device offline")
    return device
