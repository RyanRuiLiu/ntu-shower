package com.ryanliu.ntushower.core.config

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class RuntimeConfigTest {

    @Before
    fun resetDefaults() {
        RuntimeConfig.apply(DEFAULTS)
    }

    @Test
    fun apply_knownKeysAreApplied() {
        RuntimeConfig.apply(JSONObject("{\"heartbeat_interval_s\": 45, \"overlay_enabled\": false}"))
        assertEquals(45, RuntimeConfig.heartbeatIntervalS)
        assertFalse(RuntimeConfig.overlayEnabled)
    }

    @Test
    fun apply_unknownKeysAreIgnored() {
        // resetDefaults 后为 30；未知键不影响任何配置项
        RuntimeConfig.apply(JSONObject("{\"future_key\": 1}"))
        assertEquals(30, RuntimeConfig.heartbeatIntervalS)
    }

    @Test
    fun apply_coversEveryConfigKey() {
        val config = JSONObject(
            "{\"heartbeat_interval_s\": 15, \"reconnect_base_ms\": 2000, " +
                "\"reconnect_max_ms\": 30000, \"watchdog_interval_s\": 30, " +
                "\"report_debounce_ms\": 500, \"overlay_enabled\": false, " +
                "\"overlay_message\": \"维护中\", \"server_response_timeout_ms\": 8000, " +
                "\"id_verify_wait_ms\": 4000, \"home_budget_ms\": 9000, " +
                "\"poll_interval_ms\": 100, \"ui_click_delay_ms\": 150, " +
                "\"ota_check_interval_s\": 600, \"bath_guard_enabled\": false, " +
                "\"bath_exit_window_ms\": 20000, \"bath_exit_max_count\": 5, " +
                "\"bath_exit_cooldown_ms\": 300000}",
        )
        RuntimeConfig.apply(config)

        assertEquals(15, RuntimeConfig.heartbeatIntervalS)
        assertEquals(2000L, RuntimeConfig.reconnectBaseMs)
        assertEquals(30000L, RuntimeConfig.reconnectMaxMs)
        assertEquals(30L, RuntimeConfig.watchdogIntervalS)
        assertEquals(500L, RuntimeConfig.reportDebounceMs)
        assertFalse(RuntimeConfig.overlayEnabled)
        assertEquals("维护中", RuntimeConfig.overlayMessage)
        assertEquals(8000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(4000L, RuntimeConfig.idVerifyWaitMs)
        assertEquals(9000L, RuntimeConfig.homeBudgetMs)
        assertEquals(100L, RuntimeConfig.pollIntervalMs)
        assertEquals(150L, RuntimeConfig.uiClickDelayMs)
        assertEquals(600, RuntimeConfig.otaCheckIntervalS)
        assertFalse(RuntimeConfig.bathGuardEnabled)
        assertEquals(20000L, RuntimeConfig.bathExitWindowMs)
        assertEquals(5, RuntimeConfig.bathExitMaxCount)
        assertEquals(300000L, RuntimeConfig.bathExitCooldownMs)
    }

    @Test
    fun apply_missingKeysKeepCurrentValues() {
        RuntimeConfig.apply(JSONObject("{\"poll_interval_ms\": 300}"))
        assertEquals(300L, RuntimeConfig.pollIntervalMs)
        assertEquals(30, RuntimeConfig.heartbeatIntervalS) // 未下发的键保持原值
    }

    @Test
    fun defaults_matchProtocol() {
        assertEquals(30, RuntimeConfig.heartbeatIntervalS)
        assertEquals(5000L, RuntimeConfig.reconnectBaseMs)
        assertEquals(60000L, RuntimeConfig.reconnectMaxMs)
        assertEquals(60L, RuntimeConfig.watchdogIntervalS)
        assertEquals(1000L, RuntimeConfig.reportDebounceMs)
        assertTrue(RuntimeConfig.overlayEnabled)
        assertEquals("自动操作中…", RuntimeConfig.overlayMessage)
        assertEquals(10000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(5000L, RuntimeConfig.idVerifyWaitMs)
        assertEquals(10000L, RuntimeConfig.homeBudgetMs)
        assertEquals(200L, RuntimeConfig.pollIntervalMs)
        assertEquals(200L, RuntimeConfig.uiClickDelayMs)
        assertEquals(3600, RuntimeConfig.otaCheckIntervalS)
        assertTrue(RuntimeConfig.bathGuardEnabled)
        assertEquals(30000L, RuntimeConfig.bathExitWindowMs)
        assertEquals(3, RuntimeConfig.bathExitMaxCount)
        assertEquals(600000L, RuntimeConfig.bathExitCooldownMs)
    }

    @Test
    fun supportedKeysMatchesServerKeySet() {
        // 键集合是与服务端 DEFAULT_CONFIG 对账的单一来源：数量变化必须是有意的
        assertEquals(17, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_acceptsAnyJsonTypeForEveryKey() {
        // 服务端下发的值类型异常时（字符串写数字等）只能回退默认值，不得抛异常
        RuntimeConfig.supportedKeys.forEach { key ->
            listOf(true, 42, "text", 3.5).forEach { value ->
                RuntimeConfig.apply(JSONObject().put(key, value))
            }
        }
        assertEquals(17, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_doesNotDisturbUnrelatedKeys() {
        RuntimeConfig.apply(JSONObject().put("heartbeat_interval_s", 45))

        assertEquals(10000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(3, RuntimeConfig.bathExitMaxCount)
    }

    private companion object {
        // 全键默认值：保证任意测试执行顺序下 resetDefaults 都真实复位
        val DEFAULTS = JSONObject(
            "{\"heartbeat_interval_s\": 30, \"reconnect_base_ms\": 5000, " +
                "\"reconnect_max_ms\": 60000, \"watchdog_interval_s\": 60, " +
                "\"report_debounce_ms\": 1000, \"overlay_enabled\": true, " +
                "\"overlay_message\": \"自动操作中…\", \"server_response_timeout_ms\": 10000, " +
                "\"id_verify_wait_ms\": 5000, \"home_budget_ms\": 10000, " +
                "\"poll_interval_ms\": 200, \"ui_click_delay_ms\": 200, " +
                "\"ota_check_interval_s\": 3600, \"bath_guard_enabled\": true, " +
                "\"bath_exit_window_ms\": 30000, \"bath_exit_max_count\": 3, " +
                "\"bath_exit_cooldown_ms\": 600000}",
        )
    }
}
