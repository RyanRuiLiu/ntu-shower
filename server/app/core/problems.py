"""API 问题类型登记表（RFC 9457 `type` 成员的唯一来源）。

## 为什么需要它

RFC 9457 §3.1 要求 `type` 标识**问题类型**，不同问题应有不同的 URI。
若用 HTTP 状态码充当 type，`device-offline` 与 `server-overloaded` 会同为 409 +
同一个 type，客户端只能解析自由文本 `detail` 来区分——文案一改客户端就坏。

本表让每个问题有稳定 code：客户端按 `type` 分支，`title`/`detail` 只面向人。

## 文案语言

`title` 用**中文**：它是"人类可读的摘要"（RFC 9457 §3.1），而本系统的使用者
是中文用户。服务端直接给出可展示的文案，客户端**原样显示**即可，不在前端再
维护一层映射——那只会让同一句话有两处定义并随之漂移（见 AGENTS.md）。

## 约定

- code 为 kebab-case，全局唯一，一经发布不再改动（改 code 等于破坏客户端）；
- `title` 是该类型的稳定简述（不随具体请求变化）；
- 具体信息（哪个设备、什么值）放 `detail`，允许变化。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    """一个问题类型：稳定 code + HTTP 状态码 + 稳定标题。"""

    code: str
    status: int
    title: str


# ── 问题类型登记（新增类型只加常量，不改既有）──
DEVICE_NOT_FOUND = Problem("device-not-found", 404, "找不到设备")
DEVICE_OFFLINE = Problem("device-offline", 409, "设备离线")
SERVER_OVERLOADED = Problem("server-overloaded", 409, "服务端同时处理的命令过多，请稍后再试")
DOOR_NOT_FREE = Problem("door-not-free", 409, "喷头不是空闲状态")
NO_OCCUPIED_DOOR = Problem("no-occupied-door", 409, "该学号没有正在使用的喷头")
PORT_UNAVAILABLE = Problem("port-unavailable", 409, "调试通道端口被占用")
RELEASE_NOT_FOUND = Problem("release-not-found", 404, "找不到该版本")
RELEASE_TOO_LARGE = Problem("release-too-large", 413, "安装包超过体积上限")
OPS_UNAUTHORIZED = Problem("ops-unauthorized", 401, "需要访问令牌")
UNTRUSTED_DEVICE = Problem("untrusted-device", 403, "设备尚未被信任")
DUPLICATE_SESSION = Problem("duplicate-session", 409, "该学号已有进行中的喷头")

# ── 访问授权 ──
UNAUTHENTICATED = Problem("unauthenticated", 401, "需要登录")
UNKNOWN_IDENTITY = Problem("unknown-identity", 401, "该身份未被授权")
LOGIN_REJECTED = Problem("login-rejected", 401, "登录失败")
CAPABILITY_DENIED = Problem("capability-denied", 403, "权限不足")
UNKNOWN_CAPABILITY = Problem("unknown-capability", 422, "未知的权限名")

# ── 远程配置 ──
INVALID_CONFIG = Problem("invalid-config", 422, "配置值不合法")

# ── 请求格式 ──
VALIDATION_FAILED = Problem("validation-failed", 422, "提交的内容有误")

#: 供测试与文档校验的完整清单（新增类型必须登记于此表，否则自查会漏）
REGISTRY: tuple[Problem, ...] = (
    DEVICE_NOT_FOUND,
    DEVICE_OFFLINE,
    SERVER_OVERLOADED,
    DOOR_NOT_FREE,
    NO_OCCUPIED_DOOR,
    PORT_UNAVAILABLE,
    RELEASE_NOT_FOUND,
    RELEASE_TOO_LARGE,
    OPS_UNAUTHORIZED,
    UNTRUSTED_DEVICE,
    DUPLICATE_SESSION,
    UNAUTHENTICATED,
    UNKNOWN_IDENTITY,
    LOGIN_REJECTED,
    CAPABILITY_DENIED,
    UNKNOWN_CAPABILITY,
    INVALID_CONFIG,
    VALIDATION_FAILED,
)


class ApiProblemError(Exception):
    """携带登记过的问题类型的 API 异常。

    路由只抛本异常；未登记的临时问题用 `problems.adhoc_problem(...)` 显式构造，
    以免出现"随手写个字符串当错误码"的路径。
    """

    def __init__(self, problem: Problem, detail: str = "") -> None:
        super().__init__(f"{problem.code}: {detail}" if detail else problem.code)
        self.problem = problem
        self.detail = detail


def adhoc_problem(code: str, status: int, title: str, detail: str = "") -> ApiProblemError:
    """构造未登记的问题（仅用于确实一次性的场景，仍要求显式给出 code）。"""
    return ApiProblemError(Problem(code, status, title), detail)
