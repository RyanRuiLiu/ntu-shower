"""ORM 模型的汇总入口。

`Base.metadata` 只有在模型全部导入后才完整。Alembic 与环境搭建都从
这里导入，不允许直接 import 单个模型文件——漏一个模型，自动生成的迁移就会
包含一条「删表」。
"""

from __future__ import annotations

from ..db.base import Base
from .doors import DoorSnapshot, DoorState, DoorStateEvent
from .identity import AccessIdentity, AppUser, Device, DeviceCredential
from .ops import ApiCall, BackgroundTaskRun, CommandAudit, DeviceLinkEvent, DeviceLog
from .ota import OtaRelease, OtaReleaseInstall
from .settings import AppSettings, ClockShift, DeviceConfig
from .usage import UsageSegment, UsageSession
from .voice import VoiceAsset, VoiceDelivery, VoiceRule, VoiceUnit

__all__: list[str] = [
    "AccessIdentity",
    "ApiCall",
    "AppSettings",
    "AppUser",
    "BackgroundTaskRun",
    "Base",
    "ClockShift",
    "CommandAudit",
    "Device",
    "DeviceConfig",
    "DeviceCredential",
    "DeviceLinkEvent",
    "DeviceLog",
    "DoorSnapshot",
    "DoorState",
    "DoorStateEvent",
    "OtaRelease",
    "OtaReleaseInstall",
    "UsageSegment",
    "UsageSession",
    "VoiceAsset",
    "VoiceDelivery",
    "VoiceRule",
    "VoiceUnit",
]
