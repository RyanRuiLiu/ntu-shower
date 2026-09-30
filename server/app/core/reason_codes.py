"""设备命令的失败原因登记表（`reason_code` 的唯一来源）。

## 谁产生它们

三段链路各产生一部分，前缀表示**问题归谁**——不是按代码模块、也不是按执行通道：

| 前缀 | 归谁 | 例子 |
|---|---|---|
| `proto.*` | 帧本身不合法（发送方可能是任一端） | `proto.bad_json` |
| `device.*` | 设备侧 | `device.offline`、`device.a11y_unavailable` |
| `server.*` | 服务端侧 | `server.overloaded` |
| `command.*` | 某一次命令的生命周期 | `command.deadline_exceeded` |
| `door.*` | 业务规则 | `door.not_free` |
| `ui.*` | 无障碍操作没能落地 | `ui.no_target_at_point` |
| `camera.*` / `screen.*` | 那次采集没能完成 | `camera.capture_failed` |

## 为什么要有这张表

`reason_code` 是**给机器分支用的**（客户端按它决定提示什么、要不要让用户改输入），
给用户看的中文在 `terminal_message`。两者都要有：只有 code，界面上就是英文；
只有中文，客户端无法可靠分支。

散落三端行不通——改一处名字会让另一端的分支**静默失效**，而失效的表现是"提示不对"
而不是报错，没人会去留意。所以每个码都必须登记于此，由 `tests/test_reason_codes.py`
对账：它会解析设备端源码，把出现过的码全部找出来比对。

## 与 `problems.py` 的分工

`problems.py` 管的是 **HTTP 面的问题类型**（RFC 9457 的 `type`，kebab-case），
走管理 API；本表管的是**设备命令的失败原因**（点分命名空间），走 WSS 协议。
两者都会到达同一个网页端，但**格式不同是有意的**：`type` 是 URI，点号在 URI 里
不构成层级；`reason_code` 是协议字段，点号表达命名空间。不要试图合并它们。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    """一个失败原因：稳定 code + 面向人的中文说明。

    `message` 是**默认文案**，设备端遇到更具体的情况时可以提供更精确的
    `terminal_message`（例如"喷头 3 不存在"而不是"喷头不存在"）。
    """

    code: str
    message: str


# ── 帧与协议（两端都可能产生）──

BAD_JSON = Reason("proto.bad_json", "收到的帧不是合法 JSON")
UNKNOWN_TYPE = Reason("proto.unknown_type", "未知的消息类型")
HELLO_REQUIRED = Reason("proto.hello_required", "首帧必须是 link.hello")
VERSION_MISMATCH = Reason("proto.version_mismatch", "协议版本不匹配")
UNKNOWN_ACTION = Reason("proto.unknown_action", "设备不支持该操作")
UNKNOWN_COMMAND_ID = Reason("command.unknown_id", "该结果没有对应的待办命令")

# ── 设备侧 ──

DEVICE_OFFLINE = Reason("device.offline", "设备离线")
DEVICE_UNSUPPORTED_ACTION = Reason("device.unsupported_action", "设备未声明该能力，无法执行")
DEVICE_INTERNAL_ERROR = Reason("device.internal_error", "设备端内部错误")
DEVICE_EXEC_FAILED = Reason("device.exec_failed", "命令执行失败，原因见输出")
DEVICE_A11Y_UNAVAILABLE = Reason("device.a11y_unavailable", "无障碍服务不可用")
DEVICE_HOME_UNREACHABLE = Reason("device.home_unreachable", "无法回到浴室 App 首页")
DEVICE_VERIFY_PAGE_TIMEOUT = Reason("device.verify_page_timeout", "无法进入验证页面")
DEVICE_LOGIN_INPUT_FAILED = Reason("device.login_input_failed", "输入账号或密码失败")
DEVICE_BATH_SERVER_TIMEOUT = Reason("device.bath_server_timeout", "浴室服务器未在预期时间内回应")

# ── 服务端侧 ──

SERVER_OVERLOADED = Reason("server.overloaded", "服务端同时处理的命令过多，请稍后再试")

# ── 命令生命周期 ──

DEADLINE_EXCEEDED = Reason("command.deadline_exceeded", "命令超时，设备未在时限内给出结果")
COMMAND_ABORTED = Reason("command.aborted", "命令已中断：发起方断开")

# ── 业务规则 ──
#
# 与 `core.problems` 里同名的那几条措辞保持一致：同一件事在 HTTP 层与命令回报层
# 应当是同一句话，否则排障时要先判断"我看的是哪一层"。

DOOR_NOT_FREE = Reason("door.not_free", "喷头不是空闲状态")
DOOR_NOT_FOUND = Reason("door.not_found", "找不到喷头")
DOOR_NO_OCCUPIED = Reason("door.no_occupied", "该学号没有正在使用的喷头")
DOOR_INVALID_SNAPSHOT = Reason("door.invalid_snapshot", "上报的喷头快照结构不合法")

# ── 界面操作（无障碍）──

UI_NO_TARGET_AT_POINT = Reason("ui.no_target_at_point", "该位置没有可点击的控件")
UI_NO_SCROLLABLE_CONTAINER = Reason("ui.no_scrollable_container", "当前界面上没有可滚动的容器")
UI_NO_EDITABLE_FIELD = Reason("ui.no_editable_field", "没有可输入的控件")
UI_KEY_REJECTED = Reason("ui.key_rejected", "系统拒绝了该按键")
UI_UNKNOWN_KEY = Reason("ui.unknown_key", "未知的按键名")
UI_UNSUPPORTED_ACTION = Reason("ui.unsupported_action", "无障碍通道不支持该操作")
UI_NO_WINDOW = Reason("ui.no_window", "读不到当前窗口的节点树")
UI_CLICK_NO_EFFECT = Reason("ui.click_no_effect", "点击已发出，但界面没有变化")

# ── 采集 ──

CAMERA_CAPTURE_FAILED = Reason("camera.capture_failed", "拍照失败")
SCREEN_CAPTURE_FAILED = Reason("screen.capture_failed", "截屏失败")

#: 全部原因码的清单。新增原因必须登记于此，否则对账会漏。
REGISTRY: tuple[Reason, ...] = (
    BAD_JSON,
    UNKNOWN_TYPE,
    HELLO_REQUIRED,
    VERSION_MISMATCH,
    UNKNOWN_ACTION,
    UNKNOWN_COMMAND_ID,
    DEVICE_OFFLINE,
    DEVICE_UNSUPPORTED_ACTION,
    DEVICE_INTERNAL_ERROR,
    DEVICE_EXEC_FAILED,
    DEVICE_A11Y_UNAVAILABLE,
    DEVICE_HOME_UNREACHABLE,
    DEVICE_VERIFY_PAGE_TIMEOUT,
    DEVICE_LOGIN_INPUT_FAILED,
    DEVICE_BATH_SERVER_TIMEOUT,
    SERVER_OVERLOADED,
    DEADLINE_EXCEEDED,
    COMMAND_ABORTED,
    DOOR_NOT_FREE,
    DOOR_NOT_FOUND,
    DOOR_NO_OCCUPIED,
    DOOR_INVALID_SNAPSHOT,
    UI_NO_TARGET_AT_POINT,
    UI_NO_SCROLLABLE_CONTAINER,
    UI_NO_EDITABLE_FIELD,
    UI_KEY_REJECTED,
    UI_UNKNOWN_KEY,
    UI_UNSUPPORTED_ACTION,
    UI_NO_WINDOW,
    UI_CLICK_NO_EFFECT,
    CAMERA_CAPTURE_FAILED,
    SCREEN_CAPTURE_FAILED,
)

#: 原因码 → 中文说明，供"设备没给 terminal_message"时补齐。
MESSAGES: dict[str, str] = {reason.code: reason.message for reason in REGISTRY}

#: 各前缀的归属说明（前缀 → 一句话），前端据此把提示分组或选语气。
PREFIX_OWNERS: dict[str, str] = {
    "proto": "协议",
    "device": "设备",
    "server": "服务端",
    "command": "命令",
    "door": "业务",
    "ui": "界面",
    "camera": "拍照",
    "screen": "截屏",
}


def describe(reason_code: str) -> str:
    """取原因码的中文说明；未登记时给出它本身。

    未登记不该发生（有对账测试拦着），这里返回 code 是为了让漏网的那一条
    仍然能显示出来被人看见，而不是显示空白。
    """
    return MESSAGES.get(reason_code, reason_code)
