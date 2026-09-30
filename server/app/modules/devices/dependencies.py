"""设备相关的请求依赖守卫。

独立成模块而非写在 routes.py 里：这些守卫被 bridge / config / sessions 等多个
领域共用，若定义在路由模块中会造成路由模块之间互相导入（循环依赖），
进而迫使调用方使用函数内导入——那是症状，不是解法。
"""

from app.core.problems import DEVICE_NOT_FOUND, DEVICE_OFFLINE, UNTRUSTED_DEVICE, ApiProblemError

from . import service
from .schemas import DeviceRecord


def require_trusted_online_device(device_id: str) -> DeviceRecord:
    """依赖守卫：设备必须存在且已信任、当前在线；否则拒绝进入业务。"""
    device = service.get_device(device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"unknown device {device_id}")
    if not device["trusted"]:
        # 设备存在但未信任是 403，不是 404：混淆两者会让客户端以为设备不存在
        raise ApiProblemError(UNTRUSTED_DEVICE, f"device {device_id} is not trusted")
    if not service.is_online(device_id):
        raise ApiProblemError(DEVICE_OFFLINE, f"device {device_id} has no active connection")
    return device
