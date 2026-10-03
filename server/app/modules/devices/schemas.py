"""设备领域的 API 契约。

DeviceRecord 是设备的完整读模型：管理面列表、改名与信任的响应、影子文档的
reported 部分共用它。"设备不存在"以 404（DEVICE_NOT_FOUND）表达，不用响应体
里的 found: false。
"""

from typing import Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

#: 链路事件的两种取值。
#:
#: 记的是"连接建立 / 断开"这个事实，不是"设备能不能用"——后者要结合心跳看：连接在
#: 而心跳停说明是 TCP 半开，服务端靠 ping/pong 把它断掉，断开之后这里才落 offline
#: （见 gateway 的存活探针）。
LINK_ONLINE = "online"
LINK_OFFLINE = "offline"


class DeviceSummary(TypedDict):
    """设备的精简视图：可辨识与在场状态，用户面与实时推送共用。

    版本、来源 IP 等运维诊断字段不在此列，只出现在完整档案（DeviceRecord）与
    管理面接口里。device.update 的载荷即本形状加 `at` 一项（见 devices.service）。
    """

    device_id: str
    name: str
    online: bool


class DeviceListItem(DeviceSummary):
    """`GET /devices` 的一行：精简视图 + 该调用方可执行的用户动作。

    actions 由 access.user_actions 按调用方能力位与设备作用域算出（服务端唯一
    判定，客户端不复制规则）；匿名调用恒为空数组——字段仍然存在，客户端不必判键。
    """

    actions: list[str]


class DeviceRecord(TypedDict):
    """一台设备的档案。

    除 online 外各字段对应 repository 的 _DEVICE_COLUMNS；trusted 的 0/1 到布尔的
    转换在 repository 完成。online 不在档案表里，由 devices.service 从内存连接表
    填入：只有经过 service 的记录才带正确的值。
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
    """开机时刻（RFC 3339）：由设备上报的已运行秒数与服务端收到它的时刻反推。

    存时刻而不存秒数：秒数一离开采样瞬间就开始过期，开机时刻是不变的事实；
    前端用"现在减它"得到已运行时长。
    """

    clock_skew_s: float
    """设备钟与服务端的偏差（秒）。由设备回 pong 时带上的墙上时刻算出（见 core/device_clock）。

    它用来把快照里的 `open_time` / `in_time` 平移到服务端时间轴——那两个字段是"这次
    会话从什么时候开始"的唯一线索。**绝对值不可信不代表它没用**：一次会话压在 20 分钟
    以内，而 20 分钟里的漂移只有 0.03 秒，窗口内偏差是常量。
    """


class DevicePatchRequest(BaseModel):
    """改设备档案。字段都可选，只改传了的那些；改名与信任共用一个端点。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    trusted: bool | None = None


class DeviceExecRequest(BaseModel):
    """在设备上执行一条 root shell 命令。"""

    command: str = Field(min_length=1)


class DeviceClockSetRequest(BaseModel):
    """设置设备系统时钟。datetime 是设备本地墙钟时间（秒级精度）。

    浴室 App 按系统时钟装载每日定时开关机计划；设备无 NTP，开机自动校正与网页端
    手动设置共用本参数形状。格式在 HTTP 边界即拦（RFC 9457 422），不发给设备。
    """

    datetime: str = Field(pattern=r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class DeviceUiTouchRequest(BaseModel):
    """触摸：按屏幕坐标点击或滑动。

    只认坐标，不认节点序号：设备端从未有过 index 参数，坐标由调用方先经 ui.dump
    读节点树、再从 bounds 求中心点得到。节点定位由调用方承担。
    """

    x: int = Field(description="屏幕坐标 X")
    y: int = Field(description="屏幕坐标 Y")
    to_x: int | None = Field(default=None, description="滑动终点 X（与 to_y 一起用）")
    to_y: int | None = Field(default=None, description="滑动终点 Y（与 to_x 一起用）")
    duration_ms: int | None = Field(default=None, ge=1, description="滑动的按下到抬起时长")


class DeviceUiScrollRequest(BaseModel):
    """滚动可滚动容器。forward 是"往列表后面滚"，backward 相反；方向是闭集。"""

    direction: Literal["forward", "backward"] = Field(
        description="滚动方向。必填，无默认：调用方必须明确表达往哪边滚",
    )


class DeviceUiKeyRequest(BaseModel):
    """系统按键：方向键组与 Enter/Del 服务于焦点导航，其余为全局导航。"""

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

    channel 是并列的两条通路而非主备：clipboard 无障碍粘贴（支持中文，本 ROM 在
    同一字段第二次插入时会带出空格）；keys 按键注入（仅 ASCII，无空格问题）。
    由网页端使用者选择。
    """

    text: str = Field(min_length=1)
    channel: Literal["clipboard", "keys"] = Field(
        description="注入通路：clipboard 粘贴（支持中文）；keys 按键注入（仅 ASCII）。必填，调用方必须显式选择",
    )
