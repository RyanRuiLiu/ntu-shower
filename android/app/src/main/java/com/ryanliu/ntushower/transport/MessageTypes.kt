package com.ryanliu.ntushower.transport

/**
 * 协议消息类型（信封 `type` 字段的取值），按族分组。
 *
 * 与 [SupportedActions] 同一纪律：字面量只在这里出现一次，收发两侧一律引用常量——
 * GatewayClient 在链路层消化 link.* 帧、AgentService 分发业务帧，两处 when 分支改用
 * 常量之后，协议改名是编译错误而不是静默失配。取值的事实源是 protocol/schema 下的
 * 各消息 schema 与服务端网关，设备侧由 MessageTypesTest 把每个取值钉住。
 */
object MessageTypes {

    // ── 链路（link.*） ──
    const val LINK_HELLO = "link.hello"
    const val LINK_HELLO_ACK = "link.hello_ack"
    const val LINK_PING = "link.ping"
    const val LINK_PONG = "link.pong"
    const val LINK_ERROR = "link.error"

    // ── doors ──
    const val DOOR_SNAPSHOT = "door.snapshot"
    const val DOOR_SNAPSHOT_ACK = "door.snapshot_ack"

    // ── 淋浴会话 ──
    const val SESSION_COMMAND = "session.command"
    const val SESSION_COMMAND_RESULT = "session.command_result"

    // ── 配置 ──
    const val CONFIG_UPDATE = "config.update"
    const val CONFIG_ACK = "config.ack"

    // ── 楼层状态行 ──
    const val STATUS_LINE_UPDATE = "status_line.update"

    // ── 调试桥（服务端发出即返回，不等答复） ──
    const val BRIDGE_OPEN = "bridge.open"
    const val BRIDGE_CLOSE = "bridge.close"

    // ── OTA ──
    const val OTA_UPGRADE_AVAILABLE = "ota.upgrade_available"
    const val OTA_PROGRESS = "ota.progress"

    // ── 设备自报（device.*） ──
    const val DEVICE_LOG_UPLOAD = "device.log_upload"

    /** 全部消息类型。 */
    val ALL: Set<String> = setOf(
        LINK_HELLO,
        LINK_HELLO_ACK,
        LINK_PING,
        LINK_PONG,
        LINK_ERROR,
        DOOR_SNAPSHOT,
        DOOR_SNAPSHOT_ACK,
        SESSION_COMMAND,
        SESSION_COMMAND_RESULT,
        CONFIG_UPDATE,
        CONFIG_ACK,
        STATUS_LINE_UPDATE,
        BRIDGE_OPEN,
        BRIDGE_CLOSE,
        OTA_UPGRADE_AVAILABLE,
        OTA_PROGRESS,
        DEVICE_LOG_UPLOAD,
    )
}
