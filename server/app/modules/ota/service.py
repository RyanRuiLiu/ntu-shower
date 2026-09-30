"""OTA 领域服务：APK 存储与发布通知。

发布通知：release 注册后向在线的目标设备即时推送 ota.upgrade_available；
离线设备不补推——其下次轮询 manifest 时自然获取（协议 §6）。
"""

import hashlib
import os

from app.core import protocol
from app.core.config import settings
from app.gateway.manager import manager


def store_release(version: str, data: bytes) -> str:
    """APK 落盘并返回 sha256；存储目录不存在时创建。"""
    os.makedirs(settings.ota_dir, exist_ok=True)
    path = os.path.join(settings.ota_dir, f"{version}.apk")
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


async def notify_target_devices(version: str, version_code: int, mandatory: bool, target: str) -> list[str]:
    """向在线的目标设备推送升级通知，返回成功送达的 device_id 列表。"""
    if target == "all":
        targets = None  # None 表示所有在线设备
    else:
        targets = [t.strip() for t in target.split(",") if t.strip()]

    envelope = protocol.envelope(
        "ota.upgrade_available",
        {
            "version": version,
            "version_code": version_code,
            "url": f"/ota/download/{version}",
            "mandatory": mandatory,
        },
    )
    notified: list[str] = []
    for device_id, _ws in manager.online_connections():
        if targets is not None and device_id not in targets:
            continue
        if await manager.send_to(device_id, envelope):
            notified.append(device_id)
    return notified
