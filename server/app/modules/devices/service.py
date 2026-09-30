"""设备领域服务：注册表操作与在线/信任语义（在线 = 存在活动网关连接）。"""

from app.gateway.manager import manager

from . import repository


def get_device(device_id: str) -> dict | None:
    return repository.get(device_id)


def list_devices() -> list[dict]:
    return repository.list_all()


def register_device(
    device_id: str, app_version: str, version_code: int, os_version: str, capabilities: list, ip: str
) -> None:
    repository.register(device_id, app_version, version_code, os_version, capabilities, ip)


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
