"""API 问题类型登记表（RFC 9457 `type` 成员的唯一来源）。

RFC 9457 要求不同问题有不同的 type URI，客户端据此分支而不解析文案。每个问题
登记于此：code 为 kebab-case、全局唯一，一经发布不再改动；title 是稳定的中文
简述，服务端直接给出可展示的文案，客户端原样显示；具体对象放 detail，允许
变化。新增类型只加常量，不改既有。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    """一个问题类型：稳定 code、HTTP 状态码、稳定标题。"""

    code: str
    status: int
    title: str


# ── 问题类型登记 ──
# 标题是通用陈述：不带指示代词，具体的对象由 detail 指出。与 core/reason_codes
# 里同码的那几条措辞一致：同一件事在 HTTP 层与命令回报层应当是同一句话。
DEVICE_NOT_FOUND = Problem("device-not-found", 404, "找不到设备")
DEVICE_OFFLINE = Problem("device-offline", 409, "设备离线")
DEVICE_LOG_READ_FAILED = Problem("device-log-read-failed", 502, "设备日志读取失败")
SERVER_OVERLOADED = Problem("server-overloaded", 409, "服务端同时处理的命令过多，请稍后再试")
DOOR_NOT_FOUND = Problem("door-not-found", 404, "找不到喷头")
DOOR_NOT_FREE = Problem("door-not-free", 409, "喷头不是空闲状态")
NO_OCCUPIED_DOOR = Problem("no-occupied-door", 409, "该学号没有正在使用的喷头")
PORT_UNAVAILABLE = Problem("port-unavailable", 409, "调试通道端口被占用")
RELEASE_NOT_FOUND = Problem("release-not-found", 404, "找不到版本")
RELEASE_TOO_LARGE = Problem("release-too-large", 413, "安装包超过体积上限")
OTA_APK_MISMATCH = Problem("ota-apk-mismatch", 422, "APK 内容版本与发布的版本不一致")
UNTRUSTED_DEVICE = Problem("untrusted-device", 403, "设备尚未被信任")
DUPLICATE_SESSION = Problem("duplicate-session", 409, "该学号已有进行中的会话")

# ── 后台任务 ──
TASK_NOT_FOUND = Problem("task-not-found", 404, "找不到后台任务")

# ── 访问授权 ──
UNAUTHENTICATED = Problem("unauthenticated", 401, "需要身份")
UNKNOWN_IDENTITY = Problem("unknown-identity", 401, "该身份尚未被授权")
CAPABILITY_DENIED = Problem("capability-denied", 403, "权限不足")
UNKNOWN_CAPABILITY = Problem("unknown-capability", 422, "未知的权限名")

# ── 远程配置 ──
INVALID_CONFIG = Problem("invalid-config", 422, "配置值不合法")

# ── 语音播报 ──
VOICE_RULE_NOT_FOUND = Problem("voice-rule-not-found", 404, "找不到语音规则")
INVALID_VOICE_RULE = Problem("invalid-voice-rule", 422, "语音规则不合法")
VOICE_SYNTHESIS_FAILED = Problem("voice-synthesis-failed", 503, "语音合成失败")
VOICE_CLIP_NOT_FOUND = Problem("voice-clip-not-found", 404, "找不到播报音频")
INVALID_VOICE_EFFECT = Problem("invalid-voice-effect", 422, "音效不合法")
VOICE_EFFECT_NOT_FOUND = Problem("voice-effect-not-found", 404, "找不到音效")
VOICE_EFFECT_IN_USE = Problem("voice-effect-in-use", 409, "音效正在被规则使用")

# ── 请求格式 ──
VALIDATION_FAILED = Problem("validation-failed", 422, "提交的内容有误")

#: 供测试与文档校验的完整清单。新增类型必须登记于此表，否则自查会漏。
REGISTRY: tuple[Problem, ...] = (
    DEVICE_NOT_FOUND,
    DEVICE_OFFLINE,
    DEVICE_LOG_READ_FAILED,
    SERVER_OVERLOADED,
    DOOR_NOT_FOUND,
    DOOR_NOT_FREE,
    NO_OCCUPIED_DOOR,
    PORT_UNAVAILABLE,
    RELEASE_NOT_FOUND,
    RELEASE_TOO_LARGE,
    OTA_APK_MISMATCH,
    UNTRUSTED_DEVICE,
    DUPLICATE_SESSION,
    TASK_NOT_FOUND,
    UNAUTHENTICATED,
    UNKNOWN_IDENTITY,
    CAPABILITY_DENIED,
    UNKNOWN_CAPABILITY,
    INVALID_CONFIG,
    VOICE_RULE_NOT_FOUND,
    INVALID_VOICE_RULE,
    VOICE_SYNTHESIS_FAILED,
    VOICE_CLIP_NOT_FOUND,
    INVALID_VOICE_EFFECT,
    VOICE_EFFECT_NOT_FOUND,
    VOICE_EFFECT_IN_USE,
    VALIDATION_FAILED,
)


class ApiProblemError(Exception):
    """携带登记过的问题类型的 API 异常。

    路由只抛本异常；未登记的临时问题用 adhoc_problem 显式构造，避免随手写
    字符串当错误码。
    """

    def __init__(self, problem: Problem, detail: str = "") -> None:
        super().__init__(f"{problem.code}: {detail}" if detail else problem.code)
        self.problem = problem
        self.detail = detail


def adhoc_problem(code: str, status: int, title: str, detail: str = "") -> ApiProblemError:
    """构造未登记的问题，仅用于确实一次性的场景，仍要求显式给出 code。"""
    return ApiProblemError(Problem(code, status, title), detail)
