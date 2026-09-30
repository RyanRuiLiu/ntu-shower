"""设备相关的请求依赖守卫。

独立成模块而非写在 routes.py 里：这些守卫被 bridge / config / sessions 等多个
领域共用，若定义在路由模块中会造成路由模块之间互相导入（循环依赖），
进而迫使调用方使用函数内导入——那是症状，不是解法。
"""

from fastapi import HTTPException

from . import service


def require_trusted_online_device(device_id: str) -> dict:
    """依赖守卫：设备必须存在且已信任、当前在线；否则拒绝进入业务。"""
    device = service.get_device(device_id)
    if device is None or not device["trusted"]:
        raise HTTPException(status_code=404, detail="device not found")
    if not service.is_online(device_id):
        raise HTTPException(status_code=409, detail="device offline")
    return device
