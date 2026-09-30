"""API 问题类型登记表（RFC 9457 `type` 成员的唯一来源）。

## 为什么需要它

RFC 9457 §3.1 要求 `type` 标识**问题类型**，不同问题应有不同的 URI。
若用 HTTP 状态码充当 type，`device-offline` 与 `device-busy` 会同为 409 +
同一个 type，客户端只能解析自由文本 `detail` 来区分——文案一改客户端就坏。

本表让每个问题有稳定 code：客户端按 `type` 分支，`title`/`detail` 只面向人。

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
DEVICE_NOT_FOUND = Problem("device-not-found", 404, "Device not found")
DEVICE_OFFLINE = Problem("device-offline", 409, "Device offline")
DEVICE_BUSY = Problem("device-busy", 409, "Device busy")
DOOR_NOT_FREE = Problem("door-not-free", 409, "Door not free")
NO_OCCUPIED_DOOR = Problem("no-occupied-door", 409, "No occupied door for this account")
PORT_UNAVAILABLE = Problem("port-unavailable", 409, "Bridge port unavailable")
RELEASE_NOT_FOUND = Problem("release-not-found", 404, "Release not found")
RELEASE_TOO_LARGE = Problem("release-too-large", 413, "Release file too large")
ADMIN_UNAUTHORIZED = Problem("admin-unauthorized", 401, "Admin authentication required")
UNTRUSTED_DEVICE = Problem("untrusted-device", 403, "Device not trusted")
DUPLICATE_SESSION = Problem("duplicate-session", 409, "Account already has an active session")

# ── 访问授权 ──
UNAUTHENTICATED = Problem("unauthenticated", 401, "Authentication required")
UNKNOWN_IDENTITY = Problem("unknown-identity", 401, "Identity not granted access")
LOGIN_REJECTED = Problem("login-rejected", 401, "Login rejected")
CAPABILITY_DENIED = Problem("capability-denied", 403, "Insufficient capability")
UNKNOWN_CAPABILITY = Problem("unknown-capability", 422, "Unknown capability name")

#: 供测试与文档校验的完整清单（新增类型必须登记于此表，否则自查会漏）
REGISTRY: tuple[Problem, ...] = (
    DEVICE_NOT_FOUND,
    DEVICE_OFFLINE,
    DEVICE_BUSY,
    DOOR_NOT_FREE,
    NO_OCCUPIED_DOOR,
    PORT_UNAVAILABLE,
    RELEASE_NOT_FOUND,
    RELEASE_TOO_LARGE,
    ADMIN_UNAUTHORIZED,
    UNTRUSTED_DEVICE,
    DUPLICATE_SESSION,
    UNAUTHENTICATED,
    UNKNOWN_IDENTITY,
    LOGIN_REJECTED,
    CAPABILITY_DENIED,
    UNKNOWN_CAPABILITY,
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
