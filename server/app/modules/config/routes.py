"""设备远程配置：全局默认 + 设备级覆盖（docs/PROTOCOL.md §5）。

- 全局配置一份，设备可按 device_id 覆盖部分键；
- 设备连接（hello_ack）与变更推送均下发"合并后的有效配置"；
- 键集合可扩展：未知键由设备端忽略，新版本可增加键而不破坏旧设备。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.protocol import JsonObject
from app.core.security import require_admin
from app.gateway.manager import manager
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import service as sessions_service

from . import store

logger = logging.getLogger("ntu-shower.config")

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["config-admin"])


class ConfigUpdateRequest(BaseModel):
    config: dict[str, store.ConfigValue] = Field(default_factory=dict)


@admin_router.get("/config/global")
def get_global_config() -> JsonObject:
    revision, config = store.get_global()
    return {"revision": revision, "config": config}


@admin_router.put("/config/global")
async def put_global_config(req: ConfigUpdateRequest) -> JsonObject:
    """更新全局默认配置并推送给所有在线设备。"""
    revision, _config = store.put_global(dict(req.config))
    notified: list[JsonObject] = []
    for device_id, _ws in manager.online_connections():
        result = await sessions_service.push_config_update(device_id, revision, store.effective(device_id))
        notified.append({"device_id": device_id, **result})
    logger.info(
        "global config updated: revision=%d keys=%d notified=%d",
        revision,
        len(req.config),
        len(notified),
    )
    return {"revision": revision, "notified": notified}


@admin_router.get("/devices/{device_id}/config")
def get_device_config(device_id: str) -> JsonObject:
    override = store.get_device_override(device_id)
    return {
        "effective": store.effective(device_id),
        "override": {"revision": override[0], "config": override[1]} if override else None,
    }


@admin_router.put("/devices/{device_id}/config")
async def put_device_config(device_id: str, req: ConfigUpdateRequest) -> JsonObject:
    """设置设备级覆盖并推送给该设备。"""
    require_trusted_online_device(device_id)
    revision, merged = store.put_device_override(device_id, dict(req.config))
    result = await sessions_service.push_config_update(device_id, revision, merged)
    logger.info("device config updated: device=%s revision=%d keys=%d", device_id, revision, len(req.config))
    return {"device_id": device_id, "revision": revision, **result}


@admin_router.delete("/devices/{device_id}/config")
def delete_device_config(device_id: str) -> JsonObject:
    """删除设备级覆盖，回退到全局默认。"""
    removed = store.delete_device_override(device_id)
    if removed:
        logger.info("device config override removed: device=%s", device_id)
    return {"device_id": device_id, "override_removed": removed}
