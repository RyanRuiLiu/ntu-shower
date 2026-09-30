"""设备领域的 API 契约。

## 一个资源一套模型

`DeviceRecord` 是设备的**完整**读模型：管理面列表、改名/信任的响应、
影子文档的 reported 部分共用它。

此前改名与信任各有一个只含三个字段的响应模型（`DeviceRenameResult`、
`DeviceTrustResult`），与完整模型是子集关系——同一个概念在多处各写一遍字段，
改一处忘三处就漂移。现在统一返回 `DeviceRecord`，调用方拿到的始终是设备的当前全貌。

"设备不存在"也不再靠响应里的 `found: false` 表达，而是 404（`DEVICE_NOT_FOUND`）。
"""

from typing import Literal

from pydantic import BaseModel, Field
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
    booted_at: str
    """开机时刻（RFC 3339）。由设备上报的"已运行秒数"与服务端收到它的时刻反推而来。

    存时刻而不是存那串秒数：秒数一离开它的采样瞬间就开始过期，而开机时刻是个不变的事实。
    前端拿"现在减它"就能算出已运行多久，不必再让设备按周期上报。设备空着时为占位。
    """


class DevicePatchRequest(BaseModel):
    """改设备档案。字段都是可选的，只改传了的那些。

    改名与改信任位本是同一件事——"调整这台设备的档案"——因此共用一个端点，
    而不是 `/rename` 与 `/trust`、`/untrust` 三个。
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    trusted: bool | None = None


class DeviceExecRequest(BaseModel):
    """在设备上执行一条 root shell 命令。"""

    command: str = Field(min_length=1)


class DeviceUiTouchRequest(BaseModel):
    """触摸：按屏幕坐标点击或滑动。

    只认坐标。曾经有过 `index`（引用节点树里的第 n 个元素），但设备端的触摸
    命令从来没有这个参数——带 `index` 的请求会退化成坐标 (0,0)，真机上就是
    点到屏幕左上角。节点定位改由调用方承担：先 `ui.dump` 读节点树，从元素的
    `bounds` 算出中心点。`index` 是同级序号，界面一变就指向别的元素，本来也
    不该作为跨请求的引用。
    """

    x: int = Field(description="屏幕坐标 X")
    y: int = Field(description="屏幕坐标 Y")
    to_x: int | None = Field(default=None, description="滑动终点 X（与 to_y 一起用）")
    to_y: int | None = Field(default=None, description="滑动终点 Y（与 to_x 一起用）")
    duration_ms: int | None = Field(default=None, ge=1, description="滑动的按下到抬起时长")


class DeviceUiScrollRequest(BaseModel):
    """滚动可滚动容器。

    `forward` 是"往列表后面滚"，视觉上内容上移；`backward` 相反。
    方向是闭集，写错在路由层被 422 挡下，不发给设备。
    """

    direction: Literal["forward", "backward"] = Field(default="forward", description="滚动方向")


class DeviceUiKeyRequest(BaseModel):
    """系统按键。"""

    key: Literal["back", "home", "recents", "notifications"] = Field(description="按键名")


class DeviceUiTextRequest(BaseModel):
    """向当前焦点控件输入文本。"""

    text: str = Field(min_length=1)
