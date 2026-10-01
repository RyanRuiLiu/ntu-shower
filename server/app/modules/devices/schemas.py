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

    除 `online` 外都对应 repository 中 `_DEVICE_COLUMNS` 的列；`trusted` 是布尔语义，
    repository 负责把 SQLite 的 0/1 转换好再返回。

    `online` **不在档案表里**：它由 `devices.service` 从内存连接表填入，所以只有经过
    service 的记录才带正确的值——直接调 repository 拿到的是恒假的占位。原因见
    `devices/repository` 的模块注释。
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

    clock_skew_s: float
    """设备墙上时钟相对服务端的偏差（秒，正数表示设备快）。

    由 `link.pong` 里的设备时间戳与收到它的时刻算出，取 EMA 平滑后**存库**。

    它必须持久化：那台设备的墙钟可能一直不准（实测有一台快 59 分 47 秒，且系统设置里
    自动对时是关的），而设备上报的 `openTime` / `inTime` / `closeTime` 都是它的墙上
    时钟，服务端全靠这个值把它们校正到自己的基准上。只留在进程内存里的话，服务每次重启
    都会退回 0——变成"不校正"，那些时间戳被原样写库，与用服务端时钟写的字段混在一起，
    同一张表里的先后顺序就乱了（实测出现过"结束早于开始 55 分钟"）。
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
    """系统按键。方向键组与 Enter/Del 服务于焦点导航（对照旧项目 viewer 的按钮栏）。"""

    key: Literal[
        "back",
        "home",
        "recents",
        "notifications",
        "dpad_up",
        "dpad_down",
        "dpad_left",
        "dpad_right",
        "dpad_center",
        "enter",
        "del",
    ] = Field(description="按键名")


class DeviceUiTextRequest(BaseModel):
    """向当前焦点控件输入文本。

    channel 是**并列的两条通路**而非主备：`clipboard` 粘贴支持中文，但本 ROM 的
    粘贴在同一字段第二次插入时会带出空格；`keys` 按键注入（仅 ASCII）无空格问题，
    与旧项目行为一致。选哪条由网页端的使用者定。
    """

    text: str = Field(min_length=1)
    channel: Literal["clipboard", "keys"] = Field(
        description="注入通路：clipboard 粘贴（支持中文）；keys 按键注入（仅 ASCII）。必填，无缺省",
    )
