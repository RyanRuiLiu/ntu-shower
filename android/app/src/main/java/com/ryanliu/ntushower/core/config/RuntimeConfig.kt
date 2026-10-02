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
 * 每个键在这里各自钳到自己的合法区间：配置可能经旧版服务端或手工干预进来，
 * 设备作为独立进程按自己的规则取值，不依赖上游已经做过什么。
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

    /**
     * 发件箱的行数上限，超出即丢弃最旧的行。
     *
     * 默认值来自 [ConfigDefaults]（上游是服务端登记表 `spec.py`），那边刻意给得远超真实
     * 所需，于是它实际上不触发。**调小它是主动放弃历史**：快照丢了设备不会重发。
     */
    @Volatile var outboxMaxRows = ConfigDefaults.OUTBOX_MAX_ROWS
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

    /**
     * 是否记录自动化流程的分段耗时。
     *
     * 默认关：每次记录都要开一次文件，持续开启是无谓的开销，而它的用途本就是
     * "要量的时候量一轮"。见 [com.ryanliu.ntushower.features.automation.AutomationTrace]。
     */
    @Volatile var automationTraceEnabled = ConfigDefaults.AUTOMATION_TRACE_ENABLED
        private set

    /**
     * 命令去重窗口：记住多少条已执行命令的结果。
     *
     * 默认值来自 [ConfigDefaults]（上游是服务端登记表），那边刻意给得比够用大得多——窗口越小
     * 越可能把重复发来的命令当成新命令再执行一次，而分配与结算是真的再操作一次硬件。
     * 见 [com.ryanliu.ntushower.features.automation.CommandResultCache]。
     */
    @Volatile var commandDedupMaxCount = ConfigDefaults.COMMAND_DEDUP_MAX_COUNT
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
     * 把每个键复位为 [ConfigDefaults] 的值。
     *
     * 存在的原因是本对象是进程内的全局单例：一处改过之后，别处读到的不再是默认值，
     * 而测试需要在每个用例开始前拿回确定的状态。**复位必须也读 [ConfigDefaults]**——
     * 测试里另写一份默认值副本是与它必然分叉的第二个来源，而且分叉了测试照样通过
     * （它断言的是自己刚设进去的那个值）。
     *
     * 生产代码不走这里：配置一旦下发就生效到进程结束。
     */
    fun resetToDefaults() {
        heartbeatIntervalS = ConfigDefaults.HEARTBEAT_INTERVAL_S.toInt()
        reconnectBaseMs = ConfigDefaults.RECONNECT_BASE_MS
        reconnectMaxMs = ConfigDefaults.RECONNECT_MAX_MS
        watchdogIntervalS = ConfigDefaults.WATCHDOG_INTERVAL_S
        reportDebounceMs = ConfigDefaults.DOOR_REPORT_DEBOUNCE_MS
        outboxMaxRows = ConfigDefaults.OUTBOX_MAX_ROWS
        overlayEnabled = ConfigDefaults.OVERLAY_ENABLED
        overlayMessage = ConfigDefaults.OVERLAY_MESSAGE
        overlayTextSizeSp = ConfigDefaults.OVERLAY_TEXT_SIZE_SP.toInt()
        overlayTextColor = ConfigDefaults.OVERLAY_TEXT_COLOR
        overlayMarginLeftDp = ConfigDefaults.OVERLAY_MARGIN_LEFT_DP.toInt()
        automationTraceEnabled = ConfigDefaults.AUTOMATION_TRACE_ENABLED
        commandDedupMaxCount = ConfigDefaults.COMMAND_DEDUP_MAX_COUNT
        serverResponseTimeoutMs = ConfigDefaults.BATH_RESPONSE_TIMEOUT_MS
        idVerifyWaitMs = ConfigDefaults.CERT_VERIFY_WAIT_MS
        homeBudgetMs = ConfigDefaults.HOME_TIMEOUT_MS
        pollIntervalMs = ConfigDefaults.UI_POLL_INTERVAL_MS
        uiClickDelayMs = ConfigDefaults.UI_CLICK_DELAY_MS
        uiDumpMaxNodes = ConfigDefaults.UI_DUMP_MAX_NODES
        uiSwipeDurationMs = ConfigDefaults.UI_SWIPE_DURATION_MS
        deviceExecMaxOutputBytes = ConfigDefaults.DEVICE_EXEC_MAX_OUTPUT_BYTES
        otaCheckIntervalS = ConfigDefaults.OTA_CHECK_INTERVAL_S.toInt()
        bathGuardEnabled = ConfigDefaults.BATH_APP_GUARD_ENABLED
        bathExitWindowMs = ConfigDefaults.BATH_APP_EXIT_WINDOW_MS
        bathExitMaxCount = ConfigDefaults.BATH_APP_EXIT_MAX_COUNT.toInt()
        bathExitCooldownMs = ConfigDefaults.BATH_APP_EXIT_COOLDOWN_MS
    }

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
        "outbox_max_rows" to
            { c -> outboxMaxRows = atLeastOne(c.optLong("outbox_max_rows", outboxMaxRows)) },
        "overlay_enabled" to { c -> overlayEnabled = c.optBoolean("overlay_enabled", overlayEnabled) },
        "overlay_message" to { c -> overlayMessage = c.optString("overlay_message", overlayMessage) },
        "overlay_text_size_sp" to
            { c -> overlayTextSizeSp = atLeastOne(c.optInt("overlay_text_size_sp", overlayTextSizeSp)) },
        "overlay_text_color" to { c -> overlayTextColor = c.optString("overlay_text_color", overlayTextColor) },
        "overlay_margin_left_dp" to
            { c -> overlayMarginLeftDp = atLeastOne(c.optInt("overlay_margin_left_dp", overlayMarginLeftDp)) },
        "automation_trace_enabled" to
            { c -> automationTraceEnabled = c.optBoolean("automation_trace_enabled", automationTraceEnabled) },
        "command_dedup_max_count" to
            { c ->
                commandDedupMaxCount = atLeastOne(c.optLong("command_dedup_max_count", commandDedupMaxCount))
            },
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
