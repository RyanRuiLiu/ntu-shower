"""设备影子读模型：统一聚合 reported 与 desired，供 API 与扩展功能读写。

- reported：设备最后上报的状态（设备档案 + 喷头现态）；
- desired：服务端期望的设备运行配置（合并后的有效配置）；
- metadata：影子文档本身的说明信息。

两份数据分别由 devices 与 doors 领域拥有，本模块只做聚合——此前它自己写了两条
SELECT，与那两个领域的查询重复，其中设备那份还少取了 capabilities 与 last_ip。

聚合走的是那两个领域的 **service** 而不是 repository：在线状态由 devices 的 service
合并进记录（它是内存连接表，仓储够不着），`device_online` 同理。直接调仓储会拿到一个
永远为假的在线标志。
"""

from app.core.clock import now_rfc3339
from app.modules.config import store as config_store
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service

from .schemas import DeviceShadow, ShadowReported


def get_reported(device_id: str) -> ShadowReported | None:
    """reported：设备最后上报的状态（含在线标志）；设备不存在返回 None。"""
    device = devices_service.get_device(device_id)
    if device is None:
        return None
    return {"device": device, "doors": doors_service.list_doors(device_id)}


def get_shadow(device_id: str) -> DeviceShadow | None:
    """完整影子文档（管理面）：reported + desired + metadata。"""
    reported = get_reported(device_id)
    if reported is None:
        return None
    global_revision, _global_overrides = config_store.get_global_overrides()
    override = config_store.get_device_overrides(device_id)
    desired_revision = override[0] if override else global_revision
    return {
        "reported": reported,
        "desired": {"revision": desired_revision, "config": config_store.effective(device_id)},
        "metadata": {"at": now_rfc3339(), "source": "devices + door_live + device_config"},
    }
