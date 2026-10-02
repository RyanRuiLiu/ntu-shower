"""设备领域服务：档案查询、在线状态并入记录、device.update 事件的唯一发布点。

在线只有 core/connections 的连接表一个来源，档案表里没有这个列；每条返回给
调用方的记录都在这里合并一次，online 必定反映此刻的真实连接。

device.update 的 payload 是精简档案（DeviceSummary）：版本、来源 IP、时钟偏差
等运维诊断字段不进公开推送，管理台要详情走 REST。发布只经 [publish_device_update]
一处，网关生命周期与档案修改共用。
"""

from app.core import events
from app.core.clock import now_rfc3339
from app.core.connections import manager

from . import repository
from .schemas import DeviceRecord, DeviceSummary


def get_device(device_id: str) -> DeviceRecord | None:
    record = repository.get(device_id)
    if record is None:
        return None
    record["online"] = manager.is_online(device_id)
    return record


def list_devices() -> list[DeviceRecord]:
    records = repository.list_all()
    for record in records:
        record["online"] = manager.is_online(record["device_id"])
    return records


def to_summary(record: DeviceRecord) -> DeviceSummary:
    """完整档案裁剪为精简视图（字段收口于此，用户面与推送共用）。"""
    return {"device_id": record["device_id"], "name": record["name"], "online": record["online"]}


def publish_device_update(device_id: str) -> None:
    """发布一次 device.update（精简载荷）；档案不存在的设备推最小占位。"""
    record = get_device(device_id)
    if record is not None:
        payload: dict[str, object] = dict(to_summary(record))
    else:
        payload = {"device_id": device_id, "name": "", "online": False}
    payload["at"] = now_rfc3339()
    events.publish(events.DEVICE_UPDATE, payload)


def register_device(
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: str,
) -> None:
    repository.register(device_id, app_version, version_code, os_version, capabilities, ip, booted_at)


def touch_seen(device_id: str) -> None:
    """记下刚收到该设备的一帧（心跳粒度，见 repository.touch_seen）。"""
    repository.touch_seen(device_id)


def set_device_offline(device_id: str) -> None:
    """断开收尾：刷新"最后见到"时刻（列的语义是"最后见到"，不分心跳还是断开）。"""
    repository.touch_seen(device_id)


def rename_device(device_id: str, name: str) -> bool:
    return repository.set_name(device_id, name)


def set_trusted(device_id: str, trusted: bool) -> bool:
    return repository.set_trusted(device_id, trusted)


def is_online(device_id: str) -> bool:
    return manager.is_online(device_id)


def get_clock_skew(device_id: str) -> float:
    """该设备时钟偏差的读取出口（网关经服务层访问领域，不直触仓储）。"""
    return repository.get_clock_skew(device_id)


def set_clock_skew(device_id: str, skew_s: float) -> None:
    repository.set_clock_skew(device_id, skew_s)
