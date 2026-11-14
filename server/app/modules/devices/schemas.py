"""设备领域的 API 契约。

DeviceRecord 是设备的完整读模型：管理面列表、改名与信任的响应、影子文档的
reported 部分共用它。"设备不存在"以 404（DEVICE_NOT_FOUND）表达，不用响应体
里的 found: false。
"""

from typing import Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.contracts import DeviceRecord as DeviceRecord

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

    actions 由 auth.allowed_actions 按调用方权限与设备作用域算出（服务端唯一
    判定，客户端不复制规则）；匿名调用恒为空数组——字段仍然存在，客户端不必判键。
    """

    actions: list[str]


class DeviceOrderRequest(BaseModel):
    """设备展示顺序：按列表顺序写 `sort_order`，全部设备清单照此排列。"""

    order: list[str] = Field(min_length=1)


class DeviceLogView(BaseModel):
    """设备最近一次上送的日志：尾部原文与上送时刻。"""

    content: str
    uploaded_at: str


class DeviceLogLiveView(BaseModel):
    """从设备现读的日志尾部；`present=false` 表示文件尚未产生（例如从未崩溃）。"""

    content: str
    kind: str
    lines: int
    truncated: bool
    present: bool
    size_bytes: int
    read_at: str


class DeviceOrderResponse(BaseModel):
    """重排后的完整设备顺序（device_id 列表，含未出现在请求里的设备）。"""

    order: list[str]


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


class DeviceEnrollRequest(BaseModel):
    """设备自注册请求：设备首次连接凭 claim 令牌换自己的接入凭据。

    设备自报档案元数据（版本、能力）——新设备的档案行在这里建立，之后每次
    上线由 `link.hello` 刷新。`device_id` 是设备自报的 ANDROID_ID：伪造需要
    物理接触目标设备（决策 ADR-0007 的既有边界，注册不改变它）。
    """

    device_id: str = Field(min_length=1, max_length=64, description="设备标识（ANDROID_ID）")
    app_version: str = Field(max_length=32, default="")
    version_code: int = Field(ge=0, le=2_000_000_000, default=0)
    os_version: str = Field(max_length=32, default="")
    capabilities: list[str] = Field(max_length=64, default=[])


class DeviceEnrollResult(BaseModel):
    """注册结果：接入凭据明文只出现在这一响应里，服务端只留摘要。"""

    device_id: str
    token: str
