"""远程配置领域的 API 契约。

`config` 字段一律是**合并后的有效配置**（默认值叠加各级覆盖），不是库里存的
差异项——调用方关心"这台设备现在实际用什么值"，差异项是存储细节，仅在有区分
必要时单独给出（`DeviceConfigView.overrides`）。
"""

from typing_extensions import TypedDict

from .spec import ConfigKind, ConfigScope

#: 有效配置的形状：键 → 标量或字符串映射。
ConfigValues = dict[str, int | bool | str | dict[str, str]]


class ConfigSpecView(TypedDict):
    """配置登记表中的一条（`GET /admin/config/spec`）。

    客户端据此渲染控件：`kind` 决定控件形态，`minimum`/`maximum` 决定输入约束，
    `unit` 用于提示，`scope` 让界面区分"下发给设备的"与"服务端自己的"。

    `kind` / `scope` 在 OpenAPI 里会展开成枚举，客户端可以直接生成对应的联合类型。
    """

    key: str
    kind: ConfigKind
    scope: ConfigScope
    default: int | bool | str | dict[str, str]
    title: str
    description: str
    minimum: int | None
    maximum: int | None
    unit: str


class ConfigPushNotice(TypedDict, total=False):
    """一次配置推送在单个设备上的结果。

    `reason` 仅在下发失败或设备未确认时出现（例如 "ack timeout"）。
    """

    device_id: str
    notified: bool
    applied: bool
    reason: str


class GlobalConfigView(TypedDict):
    """全局配置的当前有效值。"""

    revision: int
    config: ConfigValues


class GlobalConfigUpdateReport(TypedDict):
    """全局配置更新结果，附本次推送到各在线设备的回执。"""

    revision: int
    config: ConfigValues
    notified: list[ConfigPushNotice]


class DeviceConfigView(TypedDict):
    """单台设备的配置视图。

    `overrides` 单独给出：管理员需要区分"这台设备被特殊设置过"与"它跟随全局"。
    为 None 表示该设备没有任何覆盖。
    """

    revision: int
    config: ConfigValues
    overrides: ConfigValues | None


class DeviceConfigUpdateReport(TypedDict):
    """设备级配置更新结果。

    字段与推送回执并列而非继承：ConfigPushNotice 是 total=False 的，
    继承它会让 revision 与 config 也变成可选。
    """

    device_id: str
    revision: int
    config: ConfigValues
    notified: bool
    applied: bool
    reason: str


class DeviceConfigRemovalReport(TypedDict):
    """设备级覆盖删除结果；删除后该设备回落为跟随全局。"""

    device_id: str
    override_removed: bool
    config: ConfigValues
