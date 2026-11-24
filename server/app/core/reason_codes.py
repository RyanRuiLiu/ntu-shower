"""设备命令的失败原因登记表：reason_code 的唯一来源。

reason_code 供客户端分支，中文说明供展示；散落三端会让改名一端后其余端的分支
静默失效。每个码登记于此，由 tests/test_reason_codes.py 解析设备端源码对账。

前缀表示问题归属而非代码模块：proto 帧本身不合法、device 设备侧、server 服务端
侧、command 命令生命周期、door 业务规则、ui 界面操作、camera 与 screen 采集、
voice 播报、water 水控板直控。

与 core/problems 的分工：那边管 HTTP 面的问题类型（RFC 9457 的 type，kebab-case
URI），本表管设备协议的失败原因（点分命名空间）。格式不同是有意的，不要合并。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    """一个失败原因：稳定 code 与默认中文说明。

    设备端可提供更具体的 terminal_message（如"喷头 3 不存在"），没有时用
    message 补齐。
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
DEVICE_RETIRED = Reason("device.retired", "设备已停用")
DEVICE_UNSUPPORTED_ACTION = Reason("device.unsupported_action", "设备未声明该能力，无法执行")
DEVICE_INTERNAL_ERROR = Reason("device.internal_error", "设备端内部错误")
DEVICE_EXEC_FAILED = Reason("device.exec_failed", "命令执行失败，原因见输出")
DEVICE_A11Y_UNAVAILABLE = Reason("device.a11y_unavailable", "无障碍服务不可用")
DEVICE_HOME_UNREACHABLE = Reason("device.home_unreachable", "无法回到浴室 App 首页")
DEVICE_VERIFY_PAGE_TIMEOUT = Reason("device.verify_page_timeout", "无法进入验证页面")
DEVICE_LOGIN_INPUT_FAILED = Reason("device.login_input_failed", "输入账号或密码失败")
DEVICE_BATH_SERVER_TIMEOUT = Reason("device.bath_server_timeout", "浴室服务器未在预期时间内回应")

# 免费名单读写各一个码，不合并成一个"文件操作失败"：排障时这两条指向完全不同的
# 原因——读不出来多半是路径变了，写不进去多半是权限或磁盘。
DEVICE_FREELIST_READ_FAILED = Reason("device.freelist_read_failed", "读免费名单失败")
DEVICE_FREELIST_WRITE_FAILED = Reason("device.freelist_write_failed", "写免费名单失败")

# ── 服务端侧 ──

SERVER_OVERLOADED = Reason("server.overloaded", "服务端同时处理的命令过多，请稍后再试")
PORT_BIND_FAILED = Reason("server.port_bind_failed", "调试通道端口绑定失败")

# ── 命令生命周期 ──

DEADLINE_EXCEEDED = Reason("command.deadline_exceeded", "命令超时，设备未在时限内给出结果")
COMMAND_ABORTED = Reason("command.aborted", "命令已中断：发起方断开")

# ── 业务规则 ──
# 与 core/problems 里同码的那几条措辞一致：同一件事在 HTTP 层与命令回报层应当
# 是同一句话。

DOOR_NOT_FREE = Reason("door.not_free", "喷头不是空闲状态")
DOOR_NOT_FOUND = Reason("door.not_found", "找不到喷头")
DOOR_UNOCCUPIED = Reason("door.unoccupied", "该学号没有正在使用的喷头")

# ── 界面操作（无障碍）──

UI_NO_TARGET_AT_POINT = Reason("ui.no_target_at_point", "该位置没有可点击的控件")
UI_NO_EDITABLE_FIELD = Reason("ui.no_editable_field", "没有可输入的控件")
UI_KEY_REJECTED = Reason("ui.key_rejected", "系统拒绝了该按键")
UI_UNKNOWN_KEY = Reason("ui.unknown_key", "未知的按键名")
UI_UNSUPPORTED_ACTION = Reason("ui.unsupported_action", "无障碍通道不支持该操作")
UI_NO_WINDOW = Reason("ui.no_window", "读不到当前窗口的节点树")
UI_CLICK_NO_EFFECT = Reason("ui.click_no_effect", "点击已发出，但界面没有变化")
UI_TEXT_REJECTED = Reason("ui.text_rejected", "系统拒绝了文本注入")
UI_KEYS_ASCII_ONLY = Reason("ui.keys_ascii_only", "按键通道仅支持英文、数字与符号")
UI_UNKNOWN_CHANNEL = Reason("ui.unknown_channel", "未知的输入通道")

# ── 采集 ──

CAMERA_CAPTURE_FAILED = Reason("camera.capture_failed", "拍照失败")
SCREEN_CAPTURE_FAILED = Reason("screen.capture_failed", "截屏失败")

# ── 水控板直控 ──
# 目标映射来自设备上的 data.xml waterList；板子本身没有会话/鉴权，失败原因按
# "映射找不到 / 板子连不上 / 板子不回 / 回包不合法 / 板子拒绝" 分开，排障时指向不同动作。

WATER_TARGET_NOT_FOUND = Reason("water.target_not_found", "找不到该喷头的板/门映射")
WATER_BOARD_UNREACHABLE = Reason("water.board_unreachable", "水控板不可达")
WATER_TIMEOUT = Reason("water.timeout", "水控板未在时限内回应")
WATER_FRAME_INVALID = Reason("water.frame_invalid", "水控板回包不合法")
WATER_BOARD_REJECTED = Reason("water.board_rejected", "水控板拒绝了该操作")

# ── 语音播报 ──

VOICE_PARAMS_INVALID = Reason("voice.params_invalid", "播报参数不合法")
VOICE_CLIP_DOWNLOAD_FAILED = Reason("voice.clip_download_failed", "播报音频下载失败")
VOICE_CLIP_INVALID = Reason("voice.clip_invalid", "播报音频校验失败")
VOICE_PLAY_FAILED = Reason("voice.play_failed", "播报播放失败")
VOICE_VOLUME_FAILED = Reason("voice.volume_failed", "设置播报音量失败")

#: 全部原因码的清单。新增原因必须登记于此，否则对账会漏。
REGISTRY: tuple[Reason, ...] = (
    BAD_JSON,
    UNKNOWN_TYPE,
    HELLO_REQUIRED,
    VERSION_MISMATCH,
    UNKNOWN_ACTION,
    UNKNOWN_COMMAND_ID,
    DEVICE_OFFLINE,
    DEVICE_RETIRED,
    DEVICE_UNSUPPORTED_ACTION,
    DEVICE_INTERNAL_ERROR,
    DEVICE_EXEC_FAILED,
    DEVICE_A11Y_UNAVAILABLE,
    DEVICE_HOME_UNREACHABLE,
    DEVICE_VERIFY_PAGE_TIMEOUT,
    DEVICE_LOGIN_INPUT_FAILED,
    DEVICE_BATH_SERVER_TIMEOUT,
    DEVICE_FREELIST_READ_FAILED,
    DEVICE_FREELIST_WRITE_FAILED,
    SERVER_OVERLOADED,
    PORT_BIND_FAILED,
    DEADLINE_EXCEEDED,
    COMMAND_ABORTED,
    DOOR_NOT_FREE,
    DOOR_NOT_FOUND,
    DOOR_UNOCCUPIED,
    UI_NO_TARGET_AT_POINT,
    UI_NO_EDITABLE_FIELD,
    UI_KEY_REJECTED,
    UI_UNKNOWN_KEY,
    UI_UNSUPPORTED_ACTION,
    UI_NO_WINDOW,
    UI_CLICK_NO_EFFECT,
    UI_TEXT_REJECTED,
    UI_KEYS_ASCII_ONLY,
    UI_UNKNOWN_CHANNEL,
    CAMERA_CAPTURE_FAILED,
    SCREEN_CAPTURE_FAILED,
    WATER_TARGET_NOT_FOUND,
    WATER_BOARD_UNREACHABLE,
    WATER_TIMEOUT,
    WATER_FRAME_INVALID,
    WATER_BOARD_REJECTED,
    VOICE_PARAMS_INVALID,
    VOICE_CLIP_DOWNLOAD_FAILED,
    VOICE_CLIP_INVALID,
    VOICE_PLAY_FAILED,
    VOICE_VOLUME_FAILED,
)

#: 原因码 → 中文说明，供"设备没给 terminal_message"时补齐。
MESSAGES: dict[str, str] = {reason.code: reason.message for reason in REGISTRY}


def describe(reason_code: str) -> str:
    """取原因码的中文说明；未登记的码原样返回，保证界面不出现空白。"""
    return MESSAGES.get(reason_code, reason_code)
