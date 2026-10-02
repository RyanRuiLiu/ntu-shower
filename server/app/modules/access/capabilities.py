"""访问授权：谁可以用哪些能力、对哪些设备。

## 模型

- 身份（identity）：一个不透明的字符串标识。它怎么来的（微信 openid、我方签发）
  与本项目无关：调用方在 `X-Identity` 头里带上它，服务端查身份库得能力位与设备作用域。
- 能力位（capability）：`session` / `photo` / `admin`。判定一律走
  access.dependencies.enforce，无任何特例。
- 设备作用域（device_scope）：`["*"]` 或设备 id 列表，限定该身份的能力能作用于
  哪些设备。

## 能力划分

- 用户能做的：查看（公开接口，无需能力位）、分配/结算（`session`）、拍照（`photo`）。
- 其余一切都是管理员的事（`admin`）：不单独设位，它们只出现在管理面，而管理面
  整个由 `admin` 位守着。

## 与 status-panel 的分工

微信相关事宜（code 换 openid、小程序界面可见性）都在 status-panel；本项目不含
任何微信内容，只认 `X-Identity` 字符串。
"""

from enum import Enum


class Capability(str, Enum):
    """可授予的能力。用户位与管理位并列，判定同构。

    用 `str, Enum` 而非 `StrEnum`：运行时是 Python 3.10，后者 3.11 才有。
    """

    SESSION = "session"
    """会话操作：分配 / 结算喷头。"""

    PHOTO = "photo"
    """摄像头拍照。"""

    ADMIN = "admin"
    """管理台操作：截图、远程执行、设备档案、配置、身份名单、发布、审计。"""


ALL_CAPABILITIES = frozenset(cap.value for cap in Capability)

#: 能力位 → 用户面动作（/devices/{id}/<动词> 端点）。admin 是管理台能力，不映射
#: 设备动作。此表是 /devices 列表 actions 字段与用户面路由动词的共同语义。
USER_ACTIONS: dict[Capability, tuple[str, ...]] = {
    Capability.SESSION: ("assign", "settle"),
    Capability.PHOTO: ("photo",),
    Capability.ADMIN: (),
}


def user_actions(capabilities: list[str]) -> list[str]:
    """由能力位集合推导用户面动作列表（actions 字段的唯一来源）。"""
    actions: list[str] = []
    for cap in Capability:
        if cap.value in capabilities:
            actions.extend(USER_ACTIONS[cap])
    return actions


def cap_label(cap: Capability) -> str:
    """能力位的中文名（单一来源：与枚举同处一文件）。"""
    return {
        Capability.SESSION: "会话操作（分配/结算）",
        Capability.PHOTO: "摄像头拍照",
        Capability.ADMIN: "管理台操作",
    }[cap]
