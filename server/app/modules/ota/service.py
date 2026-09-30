"""OTA 领域服务：APK 存储与发布通知。

发布通知：release 注册后向在线的目标设备即时推送 ota.upgrade_available；
离线设备不补推——其下次轮询 manifest 时自然获取（协议 §6）。
"""

import hashlib
import re
from pathlib import Path

from app.core import protocol
from app.core.config import settings
from app.gateway.manager import manager

# 发布版本号格式：SemVer 三段数字的严格子集。
# 该约束同时承担安全职责——版本号最终拼入文件路径，字符集必须排除
# 路径分隔符与 "." 连续序列，否则构成目录穿越（CWE-22）。
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"

_VERSION_RE = re.compile(VERSION_PATTERN)


class InvalidVersionError(ValueError):
    """版本号不符合发布格式。"""

    def __init__(self, version: str) -> None:
        super().__init__(f"invalid release version: {version!r} (expected {VERSION_PATTERN})")
        self.version = version


def release_path(version: str) -> Path:
    """发布文件的唯一路径构造点：先校验版本号，再拼路径。

    所有读写发布文件的代码必须经由此函数，禁止各自拼接路径。
    """
    if not _VERSION_RE.fullmatch(version):
        raise InvalidVersionError(version)
    return Path(settings.ota_dir) / f"{version}.apk"


def store_release(version: str, data: bytes) -> str:
    """APK 落盘并返回 sha256；存储目录不存在时创建。"""
    path = release_path(version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


async def notify_target_devices(version: str, version_code: int, mandatory: bool, target: str) -> list[str]:
    """向在线的目标设备推送升级通知，返回成功送达的 device_id 列表。"""
    targets = None if target == "all" else [t.strip() for t in target.split(",") if t.strip()]

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
