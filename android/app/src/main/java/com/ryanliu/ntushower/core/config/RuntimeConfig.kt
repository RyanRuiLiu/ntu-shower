package com.ryanliu.ntushower.core.config

import org.json.JSONObject

/**
 * 运行时配置：服务端 hello_ack / config.update 下发，进程内全局生效。
 *
 * ## 取值下限
 *
 * 服务端按 `spec.CONFIG_SPECS` 做业务范围校验（例如心跳间隔 5~600 秒），设备端
 * **不重复那套范围**——两处各写一份必然漂移。这里只兜住"失控"：0 或负数的时长
 * 会让 `postDelayed` 立即触发（心跳/重连风暴），所以一律抬到至少 1。
 *
 * 服务端已经拦下非法值，这一层是纵深防御：配置也可能经旧版服务端或手工干预
 * 进来，设备作为独立进程不应因此失控。
 *
 * ## 默认值
 *
 * 默认值沿用旧项目真机验证过的取值；未下发的键保持默认。
 * 键集合由 `server/tests/test_config_spec.py` 与服务端登记表对账。
 */
object RuntimeConfig {

    /**
     * 时长与计数的下限：0 会让定时任务立即重复触发。
     *
     * 只对"间隔/超时/窗口/计数"这类键使用。`ui_click_delay_ms` 与
     * `door_report_debounce_ms` 的 0 是有意义的取值（不等待、不合并），服务端的
     * 范围下界也是 0，不加钳制。
     */
    private const val MIN_POSITIVE = 1L

    private fun atLeastOne(value: Long): Long = value.coerceAtLeast(MIN_POSITIVE)

    private fun atLeastOne(value: Int): Int = value.coerceAtLeast(MIN_POSITIVE.toInt())

    // ── 通信时序 ──
    @Volatile var heartbeatIntervalS = ConfigDefaults.HEARTBEAT_INTERVAL_S.toInt()
        private set

    @Volatile var reconnectBaseMs = ConfigDefaults.RECONNECT_BASE_MS
        private set

    @Volatile var reconnectMaxMs = ConfigDefaults.RECONNECT_MAX_MS
        private set

    @Volatile var watchdogIntervalS = ConfigDefaults.WATCHDOG_INTERVAL_S
        private set

    // ── doors 采集 ──
    @Volatile var reportDebounceMs = ConfigDefaults.DOOR_REPORT_DEBOUNCE_MS
        private set

    // ── 自动化 ──
    @Volatile var overlayEnabled = ConfigDefaults.OVERLAY_ENABLED
        private set

    @Volatile var overlayMessage = ConfigDefaults.OVERLAY_MESSAGE
        private set

    // 遮罩的文字样式。默认值取自旧项目：目标设备是 1366x768 的横屏终端，字号小了在
    // 一米外看不清；文字被推到 600dp 处，正好落在提示图右侧的空白区里。
    @Volatile var overlayTextSizeSp = ConfigDefaults.OVERLAY_TEXT_SIZE_SP.toInt()
        private set

    @Volatile var overlayTextColor = ConfigDefaults.OVERLAY_TEXT_COLOR
        private set

    @Volatile var overlayMarginLeftDp = ConfigDefaults.OVERLAY_MARGIN_LEFT_DP.toInt()
        private set

    @Volatile var serverResponseTimeoutMs = ConfigDefaults.BATH_RESPONSE_TIMEOUT_MS
        private set

    @Volatile var idVerifyWaitMs = ConfigDefaults.CERT_VERIFY_WAIT_MS
        private set

    @Volatile var homeBudgetMs = ConfigDefaults.HOME_TIMEOUT_MS
        private set

    @Volatile var pollIntervalMs = ConfigDefaults.UI_POLL_INTERVAL_MS
        private set

    @Volatile var uiClickDelayMs = ConfigDefaults.UI_CLICK_DELAY_MS
        private set

    // 界面节点树单次导出的节点上限。超出即截断——上限同时决定回传体积，
    // 因此按实测的协议帧容量调整，而不是写死在代码里。
    @Volatile var uiDumpMaxNodes = ConfigDefaults.UI_DUMP_MAX_NODES
        private set

    // 滑动手势从按下到抬起的时长；过短会被识别成快速抛掷而非拖动。
    @Volatile var uiSwipeDurationMs = ConfigDefaults.UI_SWIPE_DURATION_MS
        private set

    // device.exec 单次回传的最大字节数，超出即截断。
    @Volatile var deviceExecMaxOutputBytes = ConfigDefaults.DEVICE_EXEC_MAX_OUTPUT_BYTES
        private set

    // ── OTA ──
    @Volatile var otaCheckIntervalS = ConfigDefaults.OTA_CHECK_INTERVAL_S.toInt()
        private set

    // ── 浴室 App 崩溃守护 ──
    @Volatile var bathGuardEnabled = ConfigDefaults.BATH_APP_GUARD_ENABLED
        private set

    @Volatile var bathExitWindowMs = ConfigDefaults.BATH_APP_EXIT_WINDOW_MS
        private set

    @Volatile var bathExitMaxCount = ConfigDefaults.BATH_APP_EXIT_MAX_COUNT.toInt()
        private set

    @Volatile var bathExitCooldownMs = ConfigDefaults.BATH_APP_EXIT_COOLDOWN_MS
        private set

    /**
     * 键 → 应用函数映射表：新增配置键只需加一行（复杂度不随键数增长）。
     *
     * 每个时长与计数取值都经 atLeastOne 取下限：见类注释的"取值下限"。
     */
    private val appliers: Map<String, (JSONObject) -> Unit> = mapOf(
        "heartbeat_interval_s" to
            { c -> heartbeatIntervalS = atLeastOne(c.optInt("heartbeat_interval_s", heartbeatIntervalS)) },
        "reconnect_base_ms" to
            { c -> reconnectBaseMs = atLeastOne(c.optLong("reconnect_base_ms", reconnectBaseMs)) },
        "reconnect_max_ms" to
            { c -> reconnectMaxMs = atLeastOne(c.optLong("reconnect_max_ms", reconnectMaxMs)) },
        "watchdog_interval_s" to
            { c -> watchdogIntervalS = atLeastOne(c.optLong("watchdog_interval_s", watchdogIntervalS)) },
        "door_report_debounce_ms" to
            { c -> reportDebounceMs = c.optLong("door_report_debounce_ms", reportDebounceMs) },
        "overlay_enabled" to { c -> overlayEnabled = c.optBoolean("overlay_enabled", overlayEnabled) },
        "overlay_message" to { c -> overlayMessage = c.optString("overlay_message", overlayMessage) },
        "overlay_text_size_sp" to
            { c -> overlayTextSizeSp = atLeastOne(c.optInt("overlay_text_size_sp", overlayTextSizeSp)) },
        "overlay_text_color" to { c -> overlayTextColor = c.optString("overlay_text_color", overlayTextColor) },
        "overlay_margin_left_dp" to
            { c -> overlayMarginLeftDp = atLeastOne(c.optInt("overlay_margin_left_dp", overlayMarginLeftDp)) },
        "bath_response_timeout_ms" to
            { c ->
                serverResponseTimeoutMs = atLeastOne(c.optLong("bath_response_timeout_ms", serverResponseTimeoutMs))
            },
        "cert_verify_wait_ms" to
            { c -> idVerifyWaitMs = atLeastOne(c.optLong("cert_verify_wait_ms", idVerifyWaitMs)) },
        "home_timeout_ms" to
            { c -> homeBudgetMs = atLeastOne(c.optLong("home_timeout_ms", homeBudgetMs)) },
        "ui_poll_interval_ms" to
            { c -> pollIntervalMs = atLeastOne(c.optLong("ui_poll_interval_ms", pollIntervalMs)) },
        "ui_click_delay_ms" to
            { c -> uiClickDelayMs = c.optLong("ui_click_delay_ms", uiClickDelayMs) },
        "ui_dump_max_nodes" to
            { c -> uiDumpMaxNodes = atLeastOne(c.optLong("ui_dump_max_nodes", uiDumpMaxNodes)) },
        "ui_swipe_duration_ms" to
            { c -> uiSwipeDurationMs = atLeastOne(c.optLong("ui_swipe_duration_ms", uiSwipeDurationMs)) },
        "device_exec_max_output_bytes" to
            { c ->
                deviceExecMaxOutputBytes =
                    atLeastOne(c.optLong("device_exec_max_output_bytes", deviceExecMaxOutputBytes))
            },
        "ota_check_interval_s" to
            { c -> otaCheckIntervalS = atLeastOne(c.optInt("ota_check_interval_s", otaCheckIntervalS)) },
        "bath_app_guard_enabled" to
            { c -> bathGuardEnabled = c.optBoolean("bath_app_guard_enabled", bathGuardEnabled) },
        "bath_app_exit_window_ms" to
            { c -> bathExitWindowMs = atLeastOne(c.optLong("bath_app_exit_window_ms", bathExitWindowMs)) },
        "bath_app_exit_max_count" to
            { c -> bathExitMaxCount = atLeastOne(c.optInt("bath_app_exit_max_count", bathExitMaxCount)) },
        "bath_app_exit_cooldown_ms" to
            { c -> bathExitCooldownMs = atLeastOne(c.optLong("bath_app_exit_cooldown_ms", bathExitCooldownMs)) },
    )

    /** 应用下发的配置；未知键忽略（服务端可能比设备端新，新增键由后续版本消化）。 */
    fun apply(config: JSONObject) {
        val keys = config.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            appliers[key]?.invoke(config)
        }
    }

    /**
     * 支持的配置键集合。
     *
     * 暴露出来有两个用途：测试可遍历全部键，确保每条映射都被执行；
     * 与服务端登记表对账时也以此为准（见 server/tests/test_config_spec.py）。
     */
    val supportedKeys: Set<String> get() = appliers.keys
}
