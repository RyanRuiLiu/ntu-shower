"""设备领域服务：档案查询，以及**在线状态**的唯一出口。

在线只有 `gateway.manager` 的连接表一个来源——档案表里没有这个列，仓储也不碰它。
所以每条返回给调用方的记录都在这里合并一次，拿到的 `online` 必定反映此刻的真实连接。

早先档案表另存了一个在线位，于是同一个概念有两个存放处。进程一重启，连接表空了而库里的
1 还在，前端照旧显示"在线"——一台根本没有连接的设备。
"""

from app.gateway.manager import manager

from . import repository
from .schemas import DeviceRecord


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
    repository.set_offline(device_id)


def rename_device(device_id: str, name: str) -> bool:
    return repository.set_name(device_id, name)


def set_trusted(device_id: str, trusted: bool) -> bool:
    return repository.set_trusted(device_id, trusted)


def is_trusted(device_id: str) -> bool:
    device = repository.get(device_id)
    return bool(device and device["trusted"])


def is_online(device_id: str) -> bool:
    return manager.is_online(device_id)
