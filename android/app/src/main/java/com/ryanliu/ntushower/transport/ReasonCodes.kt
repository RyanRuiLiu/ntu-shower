package com.ryanliu.ntushower.transport

/**
 * 失败原因码（协议 `session.command_result.reason_code` 的取值）。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，
 * 而 `tests/test_contract_sync.py` 也会立刻失败。要动取值或文案，改服务端的
 * `app/core/reason_codes.py` 后重新生成。
 *
 * 设备端回报时**必须**用这里的常量：服务端的 `tests/test_reason_codes.py` 会解析
 * 本目录下的 Kotlin 源码，出现未登记的码就失败。
 */
object ReasonCodes {
    /** 收到的帧不是合法 JSON */
    const val PROTO_BAD_JSON = "proto.bad_json"

    /** 未知的消息类型 */
    const val PROTO_UNKNOWN_TYPE = "proto.unknown_type"

    /** 首帧必须是 link.hello */
    const val PROTO_HELLO_REQUIRED = "proto.hello_required"

    /** 协议版本不匹配 */
    const val PROTO_VERSION_MISMATCH = "proto.version_mismatch"

    /** 设备不支持该操作 */
    const val PROTO_UNKNOWN_ACTION = "proto.unknown_action"

    /** 该结果没有对应的待办命令 */
    const val COMMAND_UNKNOWN_ID = "command.unknown_id"

    /** 设备离线 */
    const val DEVICE_OFFLINE = "device.offline"

    /** 设备未声明该能力，无法执行 */
    const val DEVICE_UNSUPPORTED_ACTION = "device.unsupported_action"

    /** 设备端内部错误 */
    const val DEVICE_INTERNAL_ERROR = "device.internal_error"

    /** 命令执行失败，原因见输出 */
    const val DEVICE_EXEC_FAILED = "device.exec_failed"

    /** 无障碍服务不可用 */
    const val DEVICE_A11Y_UNAVAILABLE = "device.a11y_unavailable"

    /** 无法回到浴室 App 首页 */
    const val DEVICE_HOME_UNREACHABLE = "device.home_unreachable"

    /** 无法进入验证页面 */
    const val DEVICE_VERIFY_PAGE_TIMEOUT = "device.verify_page_timeout"

    /** 输入账号或密码失败 */
    const val DEVICE_LOGIN_INPUT_FAILED = "device.login_input_failed"

    /** 浴室服务器未在预期时间内回应 */
    const val DEVICE_BATH_SERVER_TIMEOUT = "device.bath_server_timeout"

    /** 服务端同时处理的命令过多，请稍后再试 */
    const val SERVER_OVERLOADED = "server.overloaded"

    /** 调试通道端口绑定失败 */
    const val SERVER_PORT_BIND_FAILED = "server.port_bind_failed"

    /** 命令超时，设备未在时限内给出结果 */
    const val COMMAND_DEADLINE_EXCEEDED = "command.deadline_exceeded"

    /** 命令已中断：发起方断开 */
    const val COMMAND_ABORTED = "command.aborted"

    /** 喷头不是空闲状态 */
    const val DOOR_NOT_FREE = "door.not_free"

    /** 找不到喷头 */
    const val DOOR_NOT_FOUND = "door.not_found"

    /** 该学号没有正在使用的喷头 */
    const val DOOR_NO_OCCUPIED = "door.no_occupied"

    /** 上报的喷头快照结构不合法 */
    const val DOOR_INVALID_SNAPSHOT = "door.invalid_snapshot"

    /** 该位置没有可点击的控件 */
    const val UI_NO_TARGET_AT_POINT = "ui.no_target_at_point"

    /** 当前界面上没有可滚动的容器 */
    const val UI_NO_SCROLLABLE_CONTAINER = "ui.no_scrollable_container"

    /** 没有可输入的控件 */
    const val UI_NO_EDITABLE_FIELD = "ui.no_editable_field"

    /** 系统拒绝了该按键 */
    const val UI_KEY_REJECTED = "ui.key_rejected"

    /** 未知的按键名 */
    const val UI_UNKNOWN_KEY = "ui.unknown_key"

    /** 无障碍通道不支持该操作 */
    const val UI_UNSUPPORTED_ACTION = "ui.unsupported_action"

    /** 读不到当前窗口的节点树 */
    const val UI_NO_WINDOW = "ui.no_window"

    /** 点击已发出，但界面没有变化 */
    const val UI_CLICK_NO_EFFECT = "ui.click_no_effect"

    /** 系统拒绝了文本注入 */
    const val UI_TEXT_REJECTED = "ui.text_rejected"

    /** 按键通道仅支持英文、数字与符号 */
    const val UI_KEYS_ASCII_ONLY = "ui.keys_ascii_only"

    /** 未知的输入通道 */
    const val UI_UNKNOWN_CHANNEL = "ui.unknown_channel"

    /** 未知的滚动方向 */
    const val UI_UNKNOWN_DIRECTION = "ui.unknown_direction"

    /** 拍照失败 */
    const val CAMERA_CAPTURE_FAILED = "camera.capture_failed"

    /** 截屏失败 */
    const val SCREEN_CAPTURE_FAILED = "screen.capture_failed"
}
