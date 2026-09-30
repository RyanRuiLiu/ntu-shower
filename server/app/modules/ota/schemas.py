"""OTA 领域的 API 契约。

`OtaManifest` 用 `total=False` 而非"有更新时才带字段"的松散对象：设备轮询时
两种结果（有更新 / 已最新）由 `up_to_date` 区分，其余字段仅在有更新时出现。
把这些字段声明为可选，客户端才知道必须先判断 `up_to_date` 再取它们。
"""

from typing_extensions import TypedDict


class ReleaseRecord(TypedDict):
    """一条已注册的发布。"""

    version: str
    version_code: int
    sha256: str
    mandatory: bool
    force: bool
    target: str
    created_at: str


class OtaManifest(TypedDict, total=False):
    """设备轮询更新的应答。

    `up_to_date` 恒存在；为假时其余字段才有意义。

    `size` 供设备限制下载字节数——它按这个值收，多一个字节就中止，不让来路不明的
    东西继续往盘上写。
    """

    up_to_date: bool
    version: str
    version_code: int
    url: str
    sha256: str
    size: int
    mandatory: bool
    force: bool


class ReleasePublished(TypedDict):
    """发布注册结果（管理面）。"""

    version: str
    version_code: int
    sha256: str
    size: int
    notified_devices: list[str]


class ReleaseRearmed(TypedDict):
    """清除安装确认的结果：force 发布对该设备重新生效。"""

    version: str
    device_id: str
    rearmed: bool


class ReleaseRemoved(TypedDict):
    """下架结果（紧急止损，如终止 OTA 循环）。"""

    version: str
    deleted: bool
