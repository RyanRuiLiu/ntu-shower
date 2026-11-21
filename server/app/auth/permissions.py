"""权限字典：scopes 的唯一出处（RFC 6749 §3.3 的 scope 语义）。

scope 是 `resource:action` 字符串：资源段与 URL 资源段同一套词（AWS IAM / Google
Cloud IAM 官方的命名模式），动作段取 read/write 与该资源已有的 URL 动词。守卫按
「请求所需 scope ∈ 调用方 scope 集」判定，`*` 通配全部（AWS IAM 的 Action 通配
写法）；`*` 只授予面板与内部脚本主体，外部调用方一律给最小集（Stripe 对无限制
密钥的立场：保留但不对新用例发放）。

扩展即加一行字符串：守卫机制固定，既有密钥不受影响；字典与端点映射的对应由
tests/test_permissions.py 钉住，防止两边各自漂移。
"""

from enum import StrEnum


class Scope(StrEnum):
    """可授予的权限。成员值即 scope 字符串，OpenAPI 与密钥管理页按取值消费。"""

    SESSIONS_WRITE = "sessions:write"
    """分配 / 结算喷头（外部业务调用的主入口）。"""

    DEVICES_READ = "devices:read"
    """设备运维档案与日志的只读面。"""

    DEVICES_PHOTO = "devices:photo"
    """摄像头拍照。"""

    DEVICES_WRITE = "devices:write"
    """设备档案、信任位与全部远程操作（exec、截图、重启、界面注入）。"""

    CONFIG_READ = "config:read"
    """全局与设备配置的只读面。"""

    CONFIG_WRITE = "config:write"
    """修改全局与设备配置。"""

    FREELIST_READ = "freelist:read"
    """免费名单的只读面（设备上的那份，经命令现读）。"""

    FREELIST_WRITE = "freelist:write"
    """免费名单的整份写回。"""

    OTA_READ = "ota:read"
    """发布清单与逐设备安装记录的只读面。"""

    OTA_WRITE = "ota:write"
    """发布、下架与重推升级。"""

    BRIDGE_WRITE = "bridge:write"
    """ADB 调试通道的开、关与状态（状态读随写位，通道管理是一件事）。"""

    WATER_READ = "water:read"
    """水控板状态的只读面。"""

    WATER_WRITE = "water:write"
    """水控板直控开关。"""

    VOICE_READ = "voice:read"
    """语音规则、素材与播放记录的只读面。"""

    VOICE_WRITE = "voice:write"
    """语音规则与素材的增删改、试听与推送。"""

    JOBS_READ = "jobs:read"
    """后台任务看板。"""

    USERS_WRITE = "users:write"
    """用户与密钥管理（含口令设置与凭据签发）。"""

    AUDIT_READ = "audit:read"
    """审计查询（调用、命令、任务记录）。"""

    WILDCARD = "*"
    """全部权限（AWS IAM 对 `*` 的官方称谓 all actions）。只授予面板与内部脚本主体，不对外的密钥发放。"""


ALL_SCOPES: frozenset[str] = frozenset(scope.value for scope in Scope)

#: scope → 中文标签（密钥管理页表单的唯一来源）。
SCOPE_LABELS: dict[str, str] = {
    Scope.SESSIONS_WRITE: "会话操作（分配/结算）",
    Scope.DEVICES_READ: "设备档案与日志（读）",
    Scope.DEVICES_PHOTO: "摄像头拍照",
    Scope.DEVICES_WRITE: "设备管理（档案/远程操作）",
    Scope.CONFIG_READ: "配置（读）",
    Scope.CONFIG_WRITE: "配置（写）",
    Scope.FREELIST_READ: "免费名单（读）",
    Scope.FREELIST_WRITE: "免费名单（写）",
    Scope.OTA_READ: "发布与安装记录（读）",
    Scope.OTA_WRITE: "升级发布",
    Scope.BRIDGE_WRITE: "ADB 调试通道",
    Scope.WATER_READ: "水控板（读）",
    Scope.WATER_WRITE: "水控板直控",
    Scope.VOICE_READ: "语音播报（读）",
    Scope.VOICE_WRITE: "语音播报（管理）",
    Scope.JOBS_READ: "后台任务看板",
    Scope.USERS_WRITE: "用户与密钥管理",
    Scope.AUDIT_READ: "审计查询",
    Scope.WILDCARD: "全部权限",
}

#: scope → 用户面动作（GET /devices 的 actions 字段唯一来源）。
SCOPE_ACTIONS: dict[str, tuple[str, ...]] = {
    Scope.SESSIONS_WRITE: ("assign", "settle"),
    Scope.DEVICES_PHOTO: ("photo",),
}


def allowed_actions(scopes: list[str]) -> list[str]:
    """由 scope 集合推导用户面动作列表；判定走这里，客户端不复制规则。

    `*`（会话通道）映射到全部用户动作。
    """
    if Scope.WILDCARD in scopes:
        actions: list[str] = []
        for scope_values in SCOPE_ACTIONS.values():
            actions.extend(scope_values)
        return actions
    actions = []
    for scope in Scope:
        if scope.value in scopes:
            actions.extend(SCOPE_ACTIONS.get(scope, ()))
    return actions


def scopes_satisfied(granted: list[str], required: list[str]) -> bool:
    """请求所需 scopes 是否都被授予；`*` 通配全部。"""
    if Scope.WILDCARD in granted:
        return True
    return all(scope in granted for scope in required)


__all__: list[str] = [
    "ALL_SCOPES",
    "SCOPE_ACTIONS",
    "SCOPE_LABELS",
    "Scope",
    "allowed_actions",
    "scopes_satisfied",
]
