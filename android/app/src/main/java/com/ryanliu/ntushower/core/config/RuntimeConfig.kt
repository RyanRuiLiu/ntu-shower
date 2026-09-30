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
     * `report_debounce_ms` 的 0 是有意义的取值（不等待、不合并），服务端的
     * 范围下界也是 0，不加钳制。
     */
    private const val MIN_POSITIVE = 1L

    private fun atLeastOne(value: Long): Long = value.coerceAtLeast(MIN_POSITIVE)

    private fun atLeastOne(value: Int): Int = value.coerceAtLeast(MIN_POSITIVE.toInt())

    // ── 通信时序 ──
    @Volatile var heartbeatIntervalS = 30
        private set

    @Volatile var reconnectBaseMs = 5000L
        private set

    @Volatile var reconnectMaxMs = 60000L
        private set

    @Volatile var watchdogIntervalS = 60L
        private set

    // ── doors 采集 ──
    @Volatile var reportDebounceMs = 1000L
        private set

    // ── 自动化 ──
    @Volatile var overlayEnabled = true
        private set

    @Volatile var overlayMessage = "自动操作中…"
        private set

    @Volatile var serverResponseTimeoutMs = 10_000L
        private set

    @Volatile var idVerifyWaitMs = 5_000L
        private set

    @Volatile var homeBudgetMs = 10_000L
        private set

    @Volatile var pollIntervalMs = 200L
        private set

    @Volatile var uiClickDelayMs = 200L
        private set

    // ── OTA ──
    @Volatile var otaCheckIntervalS = 3600
        private set

    // ── 浴室 App 崩溃守护 ──
    @Volatile var bathGuardEnabled = true
        private set

    @Volatile var bathExitWindowMs = 30_000L
        private set

    @Volatile var bathExitMaxCount = 3
        private set

    @Volatile var bathExitCooldownMs = 600_000L
        private set

    /**
     * 键 → 应用函数映射表：新增配置键只需加一行（复杂度不随键数增长）。
     *
     * 每个时长与计数取值都经 atLeastOne 兜底：见类注释的"取值下限"。
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
        "report_debounce_ms" to
            { c -> reportDebounceMs = c.optLong("report_debounce_ms", reportDebounceMs) },
        "overlay_enabled" to { c -> overlayEnabled = c.optBoolean("overlay_enabled", overlayEnabled) },
        "overlay_message" to { c -> overlayMessage = c.optString("overlay_message", overlayMessage) },
        "server_response_timeout_ms" to
            { c ->
                serverResponseTimeoutMs = atLeastOne(c.optLong("server_response_timeout_ms", serverResponseTimeoutMs))
            },
        "id_verify_wait_ms" to
            { c -> idVerifyWaitMs = atLeastOne(c.optLong("id_verify_wait_ms", idVerifyWaitMs)) },
        "home_budget_ms" to
            { c -> homeBudgetMs = atLeastOne(c.optLong("home_budget_ms", homeBudgetMs)) },
        "poll_interval_ms" to
            { c -> pollIntervalMs = atLeastOne(c.optLong("poll_interval_ms", pollIntervalMs)) },
        "ui_click_delay_ms" to
            { c -> uiClickDelayMs = c.optLong("ui_click_delay_ms", uiClickDelayMs) },
        "ota_check_interval_s" to
            { c -> otaCheckIntervalS = atLeastOne(c.optInt("ota_check_interval_s", otaCheckIntervalS)) },
        "bath_guard_enabled" to
            { c -> bathGuardEnabled = c.optBoolean("bath_guard_enabled", bathGuardEnabled) },
        "bath_exit_window_ms" to
            { c -> bathExitWindowMs = atLeastOne(c.optLong("bath_exit_window_ms", bathExitWindowMs)) },
        "bath_exit_max_count" to
            { c -> bathExitMaxCount = atLeastOne(c.optInt("bath_exit_max_count", bathExitMaxCount)) },
        "bath_exit_cooldown_ms" to
            { c -> bathExitCooldownMs = atLeastOne(c.optLong("bath_exit_cooldown_ms", bathExitCooldownMs)) },
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
