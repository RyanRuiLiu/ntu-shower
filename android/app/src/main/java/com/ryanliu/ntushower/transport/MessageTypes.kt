package com.ryanliu.ntushower.transport

/**
 * 协议消息类型（信封 `type` 字段的取值），按命名空间分组。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——消息类型的唯一出处是
 * `protocol/schema/` 下的 schema 文件名：新增/改名 = 改 schema 文件后重新生成，
 * 两端登记表自动跟上。
 */
object MessageTypes {

    // ── bridge ──
    const val BRIDGE_CLOSE = "bridge.close"
    const val BRIDGE_OPEN = "bridge.open"

    // ── config ──
    const val CONFIG_UPDATE = "config.update"
    const val CONFIG_UPDATE_ACK = "config.update_ack"

    // ── device ──
    const val DEVICE_COMMAND = "device.command"
    const val DEVICE_COMMAND_RESULT = "device.command_result"
    const val DEVICE_LOG_UPLOAD = "device.log_upload"
    const val DEVICE_STATUS = "device.status"

    // ── door ──
    const val DOOR_SNAPSHOT = "door.snapshot"
    const val DOOR_SNAPSHOT_ACK = "door.snapshot_ack"

    // ── link ──
    const val LINK_ERROR = "link.error"
    const val LINK_HELLO = "link.hello"
    const val LINK_HELLO_ACK = "link.hello_ack"
    const val LINK_PING = "link.ping"
    const val LINK_PONG = "link.pong"

    // ── ota ──
    const val OTA_PROGRESS = "ota.progress"
    const val OTA_UPDATE_AVAILABLE = "ota.update_available"

    // ── status_line ──
    const val STATUS_LINE_UPDATE = "status_line.update"

    /** 全部消息类型。 */
    val ALL: Set<String> = setOf(
        BRIDGE_CLOSE,
        BRIDGE_OPEN,
        CONFIG_UPDATE,
        CONFIG_UPDATE_ACK,
        DEVICE_COMMAND,
        DEVICE_COMMAND_RESULT,
        DEVICE_LOG_UPLOAD,
        DEVICE_STATUS,
        DOOR_SNAPSHOT,
        DOOR_SNAPSHOT_ACK,
        LINK_ERROR,
        LINK_HELLO,
        LINK_HELLO_ACK,
        LINK_PING,
        LINK_PONG,
        OTA_PROGRESS,
        OTA_UPDATE_AVAILABLE,
        STATUS_LINE_UPDATE,
    )
}
