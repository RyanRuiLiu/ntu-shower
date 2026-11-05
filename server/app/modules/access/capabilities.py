"""访问授权：谁可以用哪些能力、对哪些设备。

## 模型

- 身份（identity）：一个不透明的字符串标识。它怎么来的（微信 openid、我方签发）
  与本项目无关：调用方在 `X-Identity` 头里带上它，服务端查身份库得能力位与设备作用域。
- 能力位（capability）：`session` / `photo` / `admin` / `tasks` / `stats`。
- 设备作用域（device_scope）：`["*"]` 或设备 id 列表，限定该身份的能力能作用于
  哪些设备。

## 两种能力位

- **有设备维度的**（`session` / `photo` / `admin`）：能力回答"能做什么"，作用域回答
  "对哪些设备做"，两者缺一不可，判定一律走 `access.dependencies.enforce`。
- **没有设备维度的**（`tasks` / `stats`）：它们回答的问题里没有"对哪些设备"这一维。
  `tasks` 管的是四份 `*_students` 名单，而名单是 SERVER scope、不分设备；`stats` 管的
  是"这个人能不能看统计"，而看哪台设备由页面自己选。这不是"作用域为 `*`"。
  判定走 `dependencies.require_capability`。

哪几个能力属于哪一类由 [DEVICE_SCOPED] 登记，不靠调用方记得该调哪个守卫；误用会让
`require_capability` 当场报错，而不是静默放开一个绕过作用域的洞。

## 能力划分

- 用户能做的：查看（公开接口，无需能力位）、分配/结算（`session`）、拍照（`photo`）。
- 后台任务名单维护（`tasks`）：增删任意学号在四份 `*_students` 名单里的登记。
  **不限学号归属**——服务端不校验"这个学号是不是调用方自己的"，也不校验凭证真伪，
  把人移出名单连密码都不需要。所以授予它等于授予"能停掉任何人的后台任务"，
  按半个管理员对待，别随手给。
- 浴室统计（`stats`）：读一台设备的洗浴聚合数据与任意学号的个人统计。它给的是
  **别人的使用记录**（谁在什么时候洗了多久），所以同样是"按半个管理员对待"的东西，
  而且**服务端不校验 `cert_no` 是不是调用方自己的**——与 `tasks` 同一套信任模型。
- 其余一切都是管理员的事（`admin`）：不单独设位，它们只出现在管理面，而管理面
  整个由 `admin` 位守着。

## 与 status-panel 的分工

微信相关事宜（code 换 openid、小程序界面可见性）都在 status-panel；本项目不含
任何微信内容，只认 `X-Identity` 字符串。
"""

from enum import StrEnum


class Capability(StrEnum):
    """可授予的能力。用户位与管理位并列，判定同构。

    `StrEnum`（3.11+）：成员即字符串值，OpenAPI 与跨端对账都按取值消费。
    """

    SESSION = "session"
    """会话操作：分配 / 结算喷头。"""

    PHOTO = "photo"
    """摄像头拍照。"""

    ADMIN = "admin"
    """管理台操作：截图、远程执行、设备档案、配置、身份名单、发布、审计。"""

    TASKS = "tasks"
    """后台任务名单维护：增删任意学号在 `*_students` 名单里的登记。

    名单是 SERVER scope、不分设备，所以这个能力没有设备维度——它不在 [DEVICE_SCOPED]
    里，判定走 `require_capability` 而不是 `enforce`。
    """

    STATS = "stats"
    """浴室统计：读洗浴聚合数据与任意学号的个人统计。

    与 `tasks` 同类，**没有设备维度**——看哪台设备由页面自己选，这个位回答的是
    "这个人能不能看统计"。所以它不在 [DEVICE_SCOPED] 里，判定走 `require_capability`。
    """


ALL_CAPABILITIES = frozenset(cap.value for cap in Capability)

#: 有设备维度的能力：判定必须经 enforce（能力位 × 作用域）。只问能力位等于绕开作用域，
#: 所以 `require_capability` 见到这里的能力会直接报错（见 access.dependencies）。
#: 不在这里的能力管的是不分设备的东西——"对哪些设备做"这一问对它不成立。
DEVICE_SCOPED: frozenset[Capability] = frozenset({Capability.SESSION, Capability.PHOTO, Capability.ADMIN})

#: 能力位 → 用户面动作（/devices/{id}/<动词> 端点）。admin 不映射设备动作（它是管理台），
#: tasks 与 stats 也不映射（它们管的是不分设备的东西）。此表是 /devices 列表 actions
#: 字段与用户面路由动词的共同语义。
USER_ACTIONS: dict[Capability, tuple[str, ...]] = {
    Capability.SESSION: ("assign", "settle"),
    Capability.PHOTO: ("photo",),
    Capability.ADMIN: (),
    Capability.TASKS: (),
    Capability.STATS: (),
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
        Capability.TASKS: "后台任务名单维护",
        Capability.STATS: "浴室统计查看",
    }[cap]
