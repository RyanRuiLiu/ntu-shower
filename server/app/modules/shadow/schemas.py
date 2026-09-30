"""设备影子领域的 API 契约（行业 Device Shadow 模式的文档形状）。

`reported` 复用其它领域的读模型而不是重列字段：同一份数据在两处各写一遍，
改一处必漏另一处。
"""

from typing_extensions import TypedDict

from app.core.protocol import JsonObject
from app.modules.devices.schemas import DeviceRecord
from app.modules.doors.schemas import DoorView


class ShadowReported(TypedDict):
    """设备上报态：档案 + 全部喷头的完整现态。"""

    device: DeviceRecord
    doors: list[DoorView]


class ShadowDesired(TypedDict):
    """服务端期望态：合并后的有效运行配置。"""

    revision: int
    config: JsonObject


class ShadowMetadata(TypedDict):
    """影子文档自身的说明信息。"""

    at: str
    source: str


class DeviceShadow(TypedDict):
    """完整影子文档：reported + desired + metadata。"""

    reported: ShadowReported
    desired: ShadowDesired
    metadata: ShadowMetadata
