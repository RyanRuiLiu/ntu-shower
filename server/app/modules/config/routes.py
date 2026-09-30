"""设备远程配置：全局默认 + 设备级覆盖（docs/PROTOCOL.md §5）。

- 全局配置一份，设备可按 device_id 覆盖部分键；
- 设备连接（hello_ack）与变更推送均下发"合并后的有效配置"；
- 键集合可扩展：未知键由设备端忽略，新版本可增加键而不破坏旧设备。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import require_admin

from . import store

router = APIRouter(tags=["config"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["config-admin"])


class ConfigUpdateRequest(BaseModel):
    config: dict = Field(default_factory=dict)


@admin_router.get("/config/global")
def get_global_config() -> dict:
    revision, config = store.get_global()
    return {"revision": revision, "config": config}


@admin_router.put("/config/global")
async def put_global_config(req: ConfigUpdateRequest) -> dict:
    """更新全局默认配置并推送给所有在线设备。"""
    from app.gateway.manager import manager
    from app.modules.sessions import service as sessions_service

    revision, config = store.put_global(req.config)
    notified: list[dict] = []
    for device_id, _ws in manager.online_connections():
        result = await sessions_service.push_config_update(
            device_id,
            revision,
            store.effective(device_id),
        )
        notified.append({"device_id": device_id, **result})
    return {"revision": revision, "notified": notified}


@admin_router.get("/devices/{device_id}/config")
def get_device_config(device_id: str) -> dict:
    override = store.get_device_override(device_id)
    return {
        "effective": store.effective(device_id),
        "override": {"revision": override[0], "config": override[1]} if override else None,
    }


@admin_router.put("/devices/{device_id}/config")
async def put_device_config(device_id: str, req: ConfigUpdateRequest) -> dict:
    """设置设备级覆盖并推送给该设备。"""
    from app.modules.devices.routes import require_trusted_online_device
    from app.modules.sessions import service as sessions_service

    require_trusted_online_device(device_id)
    revision, merged = store.put_device_override(device_id, req.config)
    result = await sessions_service.push_config_update(device_id, revision, merged)
    return {"device_id": device_id, "revision": revision, **result}


@admin_router.delete("/devices/{device_id}/config")
def delete_device_config(device_id: str) -> dict:
    """删除设备级覆盖，回退到全局默认。"""
    removed = store.delete_device_override(device_id)
    return {"device_id": device_id, "override_removed": removed}
