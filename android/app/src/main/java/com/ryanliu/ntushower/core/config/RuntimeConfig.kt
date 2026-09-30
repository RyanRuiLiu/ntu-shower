package com.ryanliu.ntushower.core.config

import org.json.JSONObject

/**
 * 运行时配置：服务端 hello_ack / config.update 下发，进程内全局生效。
 *
 * 默认值沿用旧项目真机验证过的取值；未下发的键保持默认；
 * 新增键在旧设备上被忽略（前向兼容），键集合见 docs/PROTOCOL.md §5。
 */
object RuntimeConfig {

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

    /** 键 → 应用函数映射表：新增配置键只需加一行（复杂度不随键数增长）。 */
    private val appliers: Map<String, (JSONObject) -> Unit> = mapOf(
        "heartbeat_interval_s" to { c -> heartbeatIntervalS = c.optInt("heartbeat_interval_s", heartbeatIntervalS) },
        "reconnect_base_ms" to { c -> reconnectBaseMs = c.optLong("reconnect_base_ms", reconnectBaseMs) },
        "reconnect_max_ms" to { c -> reconnectMaxMs = c.optLong("reconnect_max_ms", reconnectMaxMs) },
        "watchdog_interval_s" to { c -> watchdogIntervalS = c.optLong("watchdog_interval_s", watchdogIntervalS) },
        "report_debounce_ms" to { c -> reportDebounceMs = c.optLong("report_debounce_ms", reportDebounceMs) },
        "overlay_enabled" to { c -> overlayEnabled = c.optBoolean("overlay_enabled", overlayEnabled) },
        "overlay_message" to { c -> overlayMessage = c.optString("overlay_message", overlayMessage) },
        "server_response_timeout_ms" to
            { c -> serverResponseTimeoutMs = c.optLong("server_response_timeout_ms", serverResponseTimeoutMs) },
        "id_verify_wait_ms" to { c -> idVerifyWaitMs = c.optLong("id_verify_wait_ms", idVerifyWaitMs) },
        "home_budget_ms" to { c -> homeBudgetMs = c.optLong("home_budget_ms", homeBudgetMs) },
        "poll_interval_ms" to { c -> pollIntervalMs = c.optLong("poll_interval_ms", pollIntervalMs) },
        "ui_click_delay_ms" to { c -> uiClickDelayMs = c.optLong("ui_click_delay_ms", uiClickDelayMs) },
        "ota_check_interval_s" to
            { c -> otaCheckIntervalS = c.optInt("ota_check_interval_s", otaCheckIntervalS) },
        "bath_guard_enabled" to
            { c -> bathGuardEnabled = c.optBoolean("bath_guard_enabled", bathGuardEnabled) },
        "bath_exit_window_ms" to
            { c -> bathExitWindowMs = c.optLong("bath_exit_window_ms", bathExitWindowMs) },
        "bath_exit_max_count" to
            { c -> bathExitMaxCount = c.optInt("bath_exit_max_count", bathExitMaxCount) },
        "bath_exit_cooldown_ms" to
            { c -> bathExitCooldownMs = c.optLong("bath_exit_cooldown_ms", bathExitCooldownMs) },
    )

    /** 应用下发的配置；未知键忽略（前向兼容）。 */
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
     * 与服务端 DEFAULT_CONFIG 对账时也以此为准（见 docs/PROTOCOL.md §5）。
     */
    val supportedKeys: Set<String> get() = appliers.keys
}
