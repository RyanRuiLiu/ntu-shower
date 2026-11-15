"""配置域 API：全局配置、配置登记表与单台覆盖（admin）。

读取与写入都返回合并后的有效配置（config），而不是库里存的差异项——调用方
关心的是"这台设备现在实际用什么值"，差异项是存储细节。

入参先经 store.validate：未知键、类型不符、超出范围都在此拒绝（422）。配置
错误若下发到设备，后果是设备侧行为异常（例如心跳间隔为 0 会导致心跳风暴）。
"""

from fastapi import APIRouter, Depends, Security
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard, require_trusted_online_device
from app.core.connections import manager
from app.core.errors import problem_responses
from app.core.logging import log_event
from app.core.problems import INVALID_CONFIG, ApiProblemError
from app.db.deps import get_db

from . import notifier, store
from .schemas import (
    ConfigPushNotice,
    ConfigSpecCatalog,
    ConfigValues,
    DeviceConfigUpdateReport,
    DeviceConfigView,
    GlobalConfigUpdateReport,
    GlobalConfigView,
)
from .spec import GROUPS, SECTIONS, SERVER, SPECS_BY_KEY

admin_router = APIRouter(prefix="/api/v1", tags=["config"], responses=problem_responses(401, 403, 422))


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


@admin_router.get("/config/spec", dependencies=[Security(caller, scopes=["config:read"])], summary="配置登记表")
def get_config_spec() -> ConfigSpecCatalog:
    """配置登记表：每个键的类型、范围、默认值、归属、分组与说明。

    客户端据此渲染控件（布尔用开关、整数输入框带上下限），不硬编码第二份键表。
    """
    return {
        "sections": [{"key": key, "title": title} for key, title in SECTIONS],
        "groups": [{"key": g.key, "title": g.title, "section": g.section} for g in GROUPS],
        "specs": store.describe(),
    }


@admin_router.get("/config", dependencies=[Security(caller, scopes=["config:read"])], summary="全局配置现值")
def get_global_config(session: Session = Depends(get_db)) -> GlobalConfigView:
    """全局配置的当前有效值（默认值叠加全局覆盖）。"""
    revision, _overrides = store.get_global_overrides(session)
    return {"revision": revision, "config": store.global_effective(session)}


@admin_router.put("/config", dependencies=[Security(caller, scopes=["config:write"])], summary="更新全局配置并推送")
async def put_global_config(req: ConfigUpdateRequest, session: Session = Depends(get_db)) -> GlobalConfigUpdateReport:
    """更新全局覆盖，并把新的有效配置推送给所有在线设备。"""
    _before_revision, before = store.get_global_overrides(session)
    revision, overrides = store.put_global_overrides(session, _validated(req.config))
    # 服务端的键不下发，所以只有**设备域那部分真的变了**才需要惊动设备：在服务器上运行的
    # 那些改动保存即生效，再推一条与上次一模一样的 config.update 只是白跑一趟。
    # 定时器重算与设备推送都由订阅者做（写侧不 import sessions，见 notifier.py 的
    # 模块注释）；推送目标是在线设备全体，订阅者逐台并发等待，单台慢设备不拖慢整批。
    push_targets = (
        [device_id for device_id, _ws in manager.online_connections()]
        if _device_scope(before) != _device_scope(overrides)
        else []
    )
    notified = await notifier.notify(revision, push_targets)
    log_event(
        "INFO",
        "config.global-updated",
        "全局配置已更新并推送",
        revision=revision,
        changed=len(overrides),
        notified=len(notified),
    )
    return {"revision": revision, "config": store.global_effective(session), "notified": notified}


@admin_router.get(
    "/devices/{device_id}/config",
    summary="单台设备配置",
    dependencies=[Security(caller, scopes=["config:read"]), Depends(device_scope_guard)],
)
def get_device_config(device_id: str, session: Session = Depends(get_db)) -> DeviceConfigView:
    """该设备的有效配置与它自己的覆盖项（区分「已单独覆盖」与「跟随全局」）。

    `revision` 是**这台设备自身的覆盖修订号**，没有覆盖时为 0；它与全局修订号
    各自独立计数，不可相互比较。全局有效值见 `GET /api/v1/config`。
    """
    override = store.get_device_overrides(session, device_id)
    return {
        "revision": override[0] if override else 0,
        "config": store.effective(session, device_id),
        "overrides": override[1] if override else None,
    }


@admin_router.put(
    "/devices/{device_id}/config",
    dependencies=[Security(caller, scopes=["config:write"]), Depends(device_scope_guard)],
    summary="设置单台覆盖并推送",
    responses=problem_responses(404, 409),
)
async def put_device_config(
    device_id: str, req: ConfigUpdateRequest, session: Session = Depends(get_db)
) -> DeviceConfigUpdateReport:
    """设置设备级覆盖并推送给该设备。"""
    require_trusted_online_device(device_id, session)
    revision, overrides = store.put_device_overrides(session, device_id, _validated_device_overrides(req.config))
    notified = await notifier.notify(revision, [device_id])
    result = notified[0] if notified else ConfigPushNotice(device_id=device_id)
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
        "config": store.effective(session, device_id),
        "notified": result.get("notified", False),
        "applied": result.get("applied", False),
        "reason": result.get("reason", ""),
    }
