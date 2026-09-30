package com.ryanliu.ntushower.transport

/**
 * 设备支持的远程 action 集合（协议 `capabilities` 字段的唯一来源）。
 *
 * 两处用途：
 * - `link.hello` 据此向服务端宣告自身能力，服务端在下发前比对（拒绝把设备
 *   不认识的命令发过去，而不是等它落到 `proto.unknown_action`）；
 * - `AgentService.dispatchCommand` 的分支用这些常量，避免字面量散落两处。
 *
 * 新增 action 时必须同时：加常量、加进 [ALL]、在 dispatchCommand 加分支。
 */
object SupportedActions {

    const val SESSION_ASSIGN = "session.assign"
    const val SESSION_SETTLE = "session.settle"
    const val PHOTO_CAPTURE = "photo.capture"
    const val SCREEN_CAPTURE = "screen.capture"
    const val APP_RESTART = "app.restart"
    const val APP_UNINSTALL = "app.uninstall"
    const val DEBUG_EXEC = "debug.exec"
    const val BRIDGE_OPEN = "bridge.open"
    const val BRIDGE_CLOSE = "bridge.close"

    /** 全部支持的 action（上报给服务端的 capabilities）。 */
    val ALL: Set<String> = setOf(
        SESSION_ASSIGN,
        SESSION_SETTLE,
        PHOTO_CAPTURE,
        SCREEN_CAPTURE,
        APP_RESTART,
        APP_UNINSTALL,
        DEBUG_EXEC,
        BRIDGE_OPEN,
        BRIDGE_CLOSE,
    )
}
