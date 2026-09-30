"""设备管理 API：注册表管理与远程调试入口。

跨模块复用的依赖守卫定义在 dependencies.py，本模块只负责路由编排。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.protocol import JsonObject
from app.core.security import require_admin
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import service
from .dependencies import require_trusted_online_device
from .schemas import DeviceRecord

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["devices-admin"])

# 远程调试超时：设备端需完成 root shell 执行或截图编解码，给足余量
DEBUG_EXEC_TIMEOUT_S = 30.0
SCREEN_CAPTURE_TIMEOUT_S = 30.0


class DeviceNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class DeviceExecRequest(BaseModel):
    command: str = Field(min_length=1)


@admin_router.get("/devices")
def list_devices() -> list[DeviceRecord]:
    return service.list_devices()


@admin_router.post("/devices/{device_id}/trust")
def trust_device(device_id: str) -> JsonObject:
    found = service.set_trusted(device_id, True)
    return {"device_id": device_id, "trusted": True, "found": found}


@admin_router.post("/devices/{device_id}/untrust")
def untrust_device(device_id: str) -> JsonObject:
    found = service.set_trusted(device_id, False)
    return {"device_id": device_id, "trusted": False, "found": found}


@admin_router.patch("/devices/{device_id}")
def rename_device(device_id: str, req: DeviceNameRequest) -> JsonObject:
    found = service.rename_device(device_id, req.name)
    return {"device_id": device_id, "name": req.name, "found": found}


@admin_router.post("/devices/{device_id}/exec")
async def exec_command(device_id: str, req: DeviceExecRequest) -> CommandOutcome:
    """远程调试：在设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        "debug.exec",
        {"command": req.command},
        timeout_s=DEBUG_EXEC_TIMEOUT_S,
    )


@admin_router.post("/devices/{device_id}/screen")
async def capture_screen(device_id: str) -> CommandOutcome:
    """远程调试：抓取设备全屏画面，结果在 data.image_base64（JPEG，NO_WRAP）。

    设备端走 screenrecord + MediaCodec 解码首帧——本机 framebuffer/screencap
    取不到画面（硬件合成器直出），实现见 features/remote/ScreenCapture.kt。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        "screen.capture",
        {},
        timeout_s=SCREEN_CAPTURE_TIMEOUT_S,
    )
