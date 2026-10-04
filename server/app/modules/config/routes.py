"""配置域 API：全局配置、配置登记表与单台覆盖（admin）。

读取与写入都返回合并后的有效配置（config），而不是库里存的差异项——调用方
关心的是"这台设备现在实际用什么值"，差异项是存储细节。

入参先经 store.validate：未知键、类型不符、超出范围都在此拒绝（422）。配置
错误若下发到设备，后果是设备侧行为异常（例如心跳间隔为 0 会导致心跳风暴）。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.connections import manager
from app.core.logging import log_event
from app.core.problems import INVALID_CONFIG, ApiProblemError
from app.modules.access.dependencies import require_admin
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import scheduler as sessions_scheduler
from app.modules.sessions import service as sessions_service

from . import store
from .schemas import (
    ConfigPushNotice,
    ConfigSpecCatalog,
    ConfigValues,
    DeviceConfigRemovalReport,
    DeviceConfigUpdateReport,
    DeviceConfigView,
    GlobalConfigUpdateReport,
    GlobalConfigView,
)
from .spec import GROUPS, SECTIONS, SERVER, SPECS_BY_KEY

admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)], tags=["config"])


class ConfigUpdateRequest(BaseModel):
    """配置改动。允许只提交变化的键；与默认值相同的项会被忽略，不固化成覆盖。"""

    config: ConfigValues = Field(default_factory=dict)


def _device_scope(overrides: ConfigValues) -> ConfigValues:
    """只取设备域的那部分。用来判断"这次改动需不需要惊动设备"。"""
    return {key: value for key, value in overrides.items() if SPECS_BY_KEY[key].scope is not SERVER}


def _validated(values: ConfigValues) -> ConfigValues:
    """校验入参，把领域异常转成 problem+json。"""
    try:
        return store.validate(values)
    except store.InvalidConfigError as exc:
        raise ApiProblemError(INVALID_CONFIG, exc.reason) from exc


def _validated_device_overrides(values: ConfigValues) -> ConfigValues:
    """校验单台覆盖的入参，并拒掉只服务于服务端的键。

    服务端的键在服务器上运行（后台任务、链路维持、调试通道那些），它们**不按设备区分**：
    收进来之后 `device_effective` 会把它滤掉、永远不下发，而 `effective` 会把它当真——
    于是"同一份阈值、每台设备各不相同"这种既推不出去又说得出口的状态就悄悄存在了。
    与其留这么一个口子，不如在这里拒掉。
    """
    checked = _validated(values)
    server_only = sorted(key for key in checked if SPECS_BY_KEY[key].scope is SERVER)
    if server_only:
        raise ApiProblemError(INVALID_CONFIG, f"这些键在服务端运行、不按设备区分：{server_only}")
    return checked


@admin_router.get("/config/spec")
def get_config_spec() -> ConfigSpecCatalog:
    """配置登记表：每个键的类型、范围、默认值、归属、分组与说明。

    客户端据此渲染控件（布尔用开关、整数输入框带上下限），不硬编码第二份键表。
    """
    return {
        "sections": [{"key": key, "title": title} for key, title in SECTIONS],
        "groups": [{"key": g.key, "title": g.title, "section": g.section} for g in GROUPS],
        "specs": store.describe(),
    }


@admin_router.get("/config")
def get_global_config() -> GlobalConfigView:
    """全局配置的当前有效值（默认值叠加全局覆盖）。"""
    revision, _overrides = store.get_global_overrides()
    return {"revision": revision, "config": store.global_effective()}


@admin_router.put("/config")
async def put_global_config(req: ConfigUpdateRequest) -> GlobalConfigUpdateReport:
    """更新全局覆盖，并把新的有效配置推送给所有在线设备。"""
    _before_revision, before = store.get_global_overrides()
    revision, overrides = store.put_global_overrides(_validated(req.config))
    # 后台任务的阈值与名单也在配置里，而定时器是按旧值挂着的——这里立刻重算，保存即生效。
    sessions_scheduler.reload()
    notified: list[ConfigPushNotice] = []
    # 服务端的键不下发，所以只有**设备域那部分真的变了**才需要惊动设备：在服务器上运行的
    # 那些改动保存即生效，再推一条与上次一模一样的 config.update 只是白跑一趟。
    if _device_scope(before) != _device_scope(overrides):
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


@admin_router.get("/devices/{device_id}/config")
def get_device_config(device_id: str) -> DeviceConfigView:
    """该设备的有效配置与它自己的覆盖项（区分"被特殊设置过"与"跟随全局"）。

    `revision` 是**这台设备自己的覆盖修订号**，没有覆盖时为 0；它与全局那个各自计数、
    不能比大小。要看全局的走 `GET /api/v1/config`。
    """
    override = store.get_device_overrides(device_id)
    return {
        "revision": override[0] if override else 0,
        "config": store.effective(device_id),
        "overrides": override[1] if override else None,
    }


@admin_router.put("/devices/{device_id}/config")
async def put_device_config(device_id: str, req: ConfigUpdateRequest) -> DeviceConfigUpdateReport:
    """设置设备级覆盖并推送给该设备。"""
    require_trusted_online_device(device_id)
    revision, overrides = store.put_device_overrides(device_id, _validated_device_overrides(req.config))
    sessions_scheduler.reload()
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


@admin_router.delete("/devices/{device_id}/config")
async def delete_device_config(device_id: str) -> DeviceConfigRemovalReport:
    """删除设备级覆盖：该设备回落为跟随全局，并把回落后的值推给它。

    **改了生效值就得让设备知道**——与 PUT 同一条规矩。只落库不推的话，那台设备会一直
    用着旧覆盖，直到它重连（`link.hello_ack` 带回新值）、或者下一次全局配置推送顺带带上它。

    修订号取全局那个：覆盖删掉之后这台设备已经没有自己的修订号，而它此刻的生效值就是
    全局版本。而设备端的 `config.ack` 也不回传修订号，所以这个值只进日志与审计。
    """
    removed = store.delete_device_overrides(device_id)
    if not removed:
        return {"device_id": device_id, "override_removed": False, "config": store.effective(device_id)}

    # 该设备回落为跟随全局：后台任务的定时器也要按新值重算
    sessions_scheduler.reload()
    revision, _overrides = store.get_global_overrides()
    result = await sessions_service.push_config_update(device_id, revision, store.device_effective(device_id))
    log_event(
        "INFO",
        "config.device-override-removed",
        "设备覆盖已移除并推送",
        device_id=device_id,
        revision=revision,
        notified=result.get("notified", False),
    )
    return {"device_id": device_id, "override_removed": True, "config": store.effective(device_id)}
