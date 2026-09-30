"""远程配置 API（管理面）：全局默认、设备级覆盖与配置登记表。

## 响应的形状

读取与写入都返回**合并后的有效配置**（`config`），而不是库里存的差异项——
调用方关心的是"这台设备现在实际用什么值"。差异项是存储细节。

## 校验

入参先经 `store.validate`：未知键、类型不符、超出范围都在此拒绝（422）。
配置错误若下发到设备，后果是设备侧行为异常（例如心跳间隔为 0 会导致心跳风暴），
因此宁可在此失败。
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.problems import INVALID_CONFIG, ApiProblemError
from app.core.protocol import JsonObject
from app.core.security import require_admin
from app.gateway.manager import manager
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import service as sessions_service

from . import store

logger = logging.getLogger("ntu-shower.config")

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["config-admin"])


class ConfigUpdateRequest(BaseModel):
    """配置改动。允许只提交变化的键；与默认值相同的项会被忽略，不固化成覆盖。"""

    config: dict[str, int | bool | str | dict[str, str]] = Field(default_factory=dict)


def _validated(values: dict[str, int | bool | str | dict[str, str]]) -> store.ConfigValues:
    """校验入参，把领域异常转成 problem+json。"""
    try:
        return store.validate(values)
    except store.InvalidConfigError as exc:
        raise ApiProblemError(INVALID_CONFIG, exc.reason) from exc


@admin_router.get("/config/spec")
def get_config_spec() -> list[store.ConfigSpecView]:
    """配置登记表：每个键的类型、范围、默认值、归属与说明。

    客户端据此渲染控件（布尔用开关、整数输入框带上下限），无需各自硬编码键表；
    这份描述由 CONFIG_SPECS 生成，改了代码不会忘记同步文档。
    """
    return store.describe()


@admin_router.get("/config/global")
def get_global_config() -> JsonObject:
    """全局配置的当前有效值（默认值叠加全局覆盖）。"""
    revision, _overrides = store.get_global_overrides()
    return {"revision": revision, "config": store.global_effective()}


@admin_router.put("/config/global")
async def put_global_config(req: ConfigUpdateRequest) -> JsonObject:
    """更新全局覆盖，并把新的有效配置推送给所有在线设备。"""
    revision, overrides = store.put_global_overrides(_validated(req.config))
    notified: list[JsonObject] = []
    for device_id, _ws in manager.online_connections():
        result = await sessions_service.push_config_update(device_id, revision, store.device_effective(device_id))
        notified.append({"device_id": device_id, **result})
    logger.info(
        "global config updated: revision=%d changed=%d notified=%d",
        revision,
        len(overrides),
        len(notified),
    )
    return {"revision": revision, "config": store.global_effective(), "notified": notified}


@admin_router.get("/devices/{device_id}/config")
def get_device_config(device_id: str) -> JsonObject:
    """该设备的有效配置与它自己的覆盖项。

    `overrides` 单独给出：管理员需要区分"这台设备被特殊设置过"与"它跟随全局"。
    """
    override = store.get_device_overrides(device_id)
    revision = override[0] if override else store.get_global_overrides()[0]
    return {
        "revision": revision,
        "config": store.effective(device_id),
        "overrides": override[1] if override else None,
    }


@admin_router.put("/devices/{device_id}/config")
async def put_device_config(device_id: str, req: ConfigUpdateRequest) -> JsonObject:
    """设置设备级覆盖并推送给该设备。"""
    require_trusted_online_device(device_id)
    revision, overrides = store.put_device_overrides(device_id, _validated(req.config))
    result = await sessions_service.push_config_update(device_id, revision, store.device_effective(device_id))
    logger.info("device config updated: device=%s revision=%d changed=%d", device_id, revision, len(overrides))
    return {"device_id": device_id, "revision": revision, "config": store.effective(device_id), **result}


@admin_router.delete("/devices/{device_id}/config")
def delete_device_config(device_id: str) -> JsonObject:
    """删除设备级覆盖，该设备回落为跟随全局。"""
    removed = store.delete_device_overrides(device_id)
    if removed:
        logger.info("device config override removed: device=%s", device_id)
    return {"device_id": device_id, "override_removed": removed, "config": store.effective(device_id)}
