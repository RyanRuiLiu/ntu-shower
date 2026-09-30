"""设备领域的 API 契约。

`DeviceRecord` 是设备的**完整**读模型：管理面列表、影子文档的 reported 部分
共用它，避免同一张表出现两份字段子集（此前 shadow 只取其中八列，与列表接口
不一致，前端要看能力必须再请求一次）。
"""

from typing_extensions import TypedDict


class DeviceRecord(TypedDict):
    """一台设备的档案。

    对应 repository 中 `_DEVICE_COLUMNS` 的列；`trusted` / `online` 是布尔语义，
    repository 负责把 SQLite 的 0/1 转换好再返回。
    """

    device_id: str
    name: str
    trusted: bool
    app_version: str
    version_code: int
    os_version: str
    capabilities: list[str]
    online: bool
    last_seen_at: str
    last_ip: str


class DeviceTrustResult(TypedDict):
    """信任位变更结果。

    `found` 为假表示库里没有这台设备的档案——信任只能在已有档案上操作，
    设备必须先连过一次才会建档。
    """

    device_id: str
    trusted: bool
    found: bool


class DeviceRenameResult(TypedDict):
    """改名结果。"""

    device_id: str
    name: str
    found: bool
