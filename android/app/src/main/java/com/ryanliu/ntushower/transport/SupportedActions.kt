package com.ryanliu.ntushower.transport

/**
 * 设备支持的远程 action 集合（协议 `capabilities` 字段的唯一来源）。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，
 * 而 `tests/test_contract_sync.py` 也会立刻失败。要动动作清单，改服务端的
 * `app/core/actions.py` 后重新生成。
 *
 * 两处用途：
 * - `link.hello` 据此向服务端宣告自身能力，服务端在下发前比对（拒绝把设备
 *   不认识的命令发过去，而不是等它落到 `proto.unknown_action`）；
 * - `AgentService.dispatchCommand` 的分支用这些常量，避免字面量散落两处。
 *
 * 命名规则 `<对象>.<动作>`：对象是**被操作的东西**，不是代码模块，也不是用途。
 */
object SupportedActions {

    // ── 淋浴会话 ──
    const val SESSION_ASSIGN = "session.assign"
    const val SESSION_SETTLE = "session.settle"

    // ── camera ──
    const val CAMERA_CAPTURE = "camera.capture"

    // ── 设备屏幕 ──
    const val SCREEN_CAPTURE = "screen.capture"

    // ── 设备自身 ──
    const val DEVICE_EXEC = "device.exec"
    const val DEVICE_REBOOT = "device.reboot"

    // ── app ──
    const val APP_RESTART = "app.restart"

    // ── bath ──
    const val BATH_RESTART = "bath.restart"

    // ── clock ──
    const val CLOCK_SET = "clock.set"

    // ── 调试通道 ──
    const val BRIDGE_OPEN = "bridge.open"
    const val BRIDGE_CLOSE = "bridge.close"

    // ── 设备界面（无障碍） ──
    const val UI_DUMP = "ui.dump"
    const val UI_TOUCH = "ui.touch"
    const val UI_SCROLL = "ui.scroll"
    const val UI_KEY = "ui.key"
    const val UI_INPUT = "ui.input"

    /** 全部支持的 action（上报给服务端的 capabilities）。 */
    val ALL: Set<String> = setOf(
        SESSION_ASSIGN,
        SESSION_SETTLE,
        CAMERA_CAPTURE,
        SCREEN_CAPTURE,
        DEVICE_EXEC,
        DEVICE_REBOOT,
        APP_RESTART,
        BATH_RESTART,
        CLOCK_SET,
        BRIDGE_OPEN,
        BRIDGE_CLOSE,
        UI_DUMP,
        UI_TOUCH,
        UI_SCROLL,
        UI_KEY,
        UI_INPUT,
    )
}
