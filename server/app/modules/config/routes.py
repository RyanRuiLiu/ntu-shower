"""远程配置 API（管理面）：全局默认、设备级覆盖与配置登记表。

## 响应的形状

读取与写入都返回**合并后的有效配置**（`config`），而不是库里存的差异项——
调用方关心的是"这台设备现在实际用什么值"。差异项是存储细节。

## 校验

入参先经 `store.validate`：未知键、类型不符、超出范围都在此拒绝（422）。
配置错误若下发到设备，后果是设备侧行为异常（例如心跳间隔为 0 会导致心跳风暴），
因此宁可在此失败。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.logging import log_event
from app.core.problems import INVALID_CONFIG, ApiProblemError
from app.core.security import require_ops_token
from app.gateway.manager import manager
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import service as sessions_service

from . import store
from .schemas import (
    ConfigPushNotice,
    ConfigSpecView,
    ConfigValues,
    DeviceConfigRemovalReport,
    DeviceConfigUpdateReport,
    DeviceConfigView,
    GlobalConfigUpdateReport,
    GlobalConfigView,
)

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["config-ops"])


class ConfigUpdateRequest(BaseModel):
    """配置改动。允许只提交变化的键；与默认值相同的项会被忽略，不固化成覆盖。"""

    config: ConfigValues = Field(default_factory=dict)


def _validated(values: ConfigValues) -> ConfigValues:
    """校验入参，把领域异常转成 problem+json。"""
    try:
        return store.validate(values)
    except store.InvalidConfigError as exc:
        raise ApiProblemError(INVALID_CONFIG, exc.reason) from exc


@ops_router.get("/config/spec")
def get_config_spec() -> list[ConfigSpecView]:
    """配置登记表：每个键的类型、范围、默认值、归属与说明。

    客户端据此渲染控件（布尔用开关、整数输入框带上下限），无需各自硬编码键表；
    这份描述由 spec.CONFIG_SPECS 生成，改了代码不会忘记同步文档。
    """
    return store.describe()


@ops_router.get("/config/global")
def get_global_config() -> GlobalConfigView:
    """全局配置的当前有效值（默认值叠加全局覆盖）。"""
    revision, _overrides = store.get_global_overrides()
    return {"revision": revision, "config": store.global_effective()}


@ops_router.put("/config/global")
async def put_global_config(req: ConfigUpdateRequest) -> GlobalConfigUpdateReport:
    """更新全局覆盖，并把新的有效配置推送给所有在线设备。"""
    revision, overrides = store.put_global_overrides(_validated(req.config))
    notified: list[ConfigPushNotice] = []
    for device_id, _ws in manager.online_connections():
        result = await sessions_service.push_config_update(device_id, revision, store.device_effective(device_id))
        notified.append(
            {
                "device_id": device_id,
                "notified": result.get("notified", False),
                "applied": result.get("applied", False),
                "reason": result.get("reason", ""),
            }
        )
    log_event(
        "INFO",
        "config.global-updated",
        "全局配置已更新并推送",
        revision=revision,
        changed=len(overrides),
        notified=len(notified),
    )
    return {"revision": revision, "config": store.global_effective(), "notified": notified}


@ops_router.get("/devices/{device_id}/config")
def get_device_config(device_id: str) -> DeviceConfigView:
    """该设备的有效配置与它自己的覆盖项。

    `overrides` 单独给出：需要区分"这台设备被特殊设置过"与"它跟随全局"。
    """
    override = store.get_device_overrides(device_id)
    revision = override[0] if override else store.get_global_overrides()[0]
    return {
        "revision": revision,
        "config": store.effective(device_id),
        "overrides": override[1] if override else None,
    }


@ops_router.put("/devices/{device_id}/config")
async def put_device_config(device_id: str, req: ConfigUpdateRequest) -> DeviceConfigUpdateReport:
    """设置设备级覆盖并推送给该设备。"""
    require_trusted_online_device(device_id)
    revision, overrides = store.put_device_overrides(device_id, _validated(req.config))
    result = await sessions_service.push_config_update(device_id, revision, store.device_effective(device_id))
    log_event(
        "INFO",
        "config.device-updated",
        "设备配置已更新并推送",
        device_id=device_id,
        revision=revision,
        changed=len(overrides),
    )
    return {
        "device_id": device_id,
        "revision": revision,
        "config": store.effective(device_id),
        "notified": result.get("notified", False),
        "applied": result.get("applied", False),
        "reason": result.get("reason", ""),
    }


@ops_router.delete("/devices/{device_id}/config")
def delete_device_config(device_id: str) -> DeviceConfigRemovalReport:
    """删除设备级覆盖，该设备回落为跟随全局。"""
    removed = store.delete_device_overrides(device_id)
    if removed:
        log_event("INFO", "config.device-override-removed", "设备覆盖配置已移除", device_id=device_id)
    return {"device_id": device_id, "override_removed": removed, "config": store.effective(device_id)}
