package com.ryanliu.ntushower.core.config

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Test

class RuntimeConfigTest {

    @Before
    fun resetDefaults() {
        // 复位读的是 ConfigDefaults（唯一来源）。此前这里是测试内手写的一份默认值副本，
        // 它已经与生成的那份漂移（overlay_message、ui_poll_interval_ms 都不同），还少 7 个键
        // ——被 apply_acceptsAnyJsonTypeForEveryKey 污染之后不复位，测试之间互相干扰。
        RuntimeConfig.resetToDefaults()
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
                "\"door_report_debounce_ms\": 500, \"overlay_enabled\": false, " +
                "\"overlay_message\": \"维护中\", \"bath_response_timeout_ms\": 8000, " +
                "\"cert_verify_wait_ms\": 4000, \"home_timeout_ms\": 9000, " +
                "\"ui_poll_interval_ms\": 100, \"ui_click_delay_ms\": 150, " +
                "\"ota_check_interval_s\": 600, \"bath_app_guard_enabled\": false, " +
                "\"bath_app_exit_window_ms\": 20000, \"bath_app_exit_max_count\": 5, " +
                "\"bath_app_exit_cooldown_ms\": 300000}",
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
        RuntimeConfig.apply(JSONObject("{\"ui_poll_interval_ms\": 300}"))
        assertEquals(300L, RuntimeConfig.pollIntervalMs)
        assertEquals(30, RuntimeConfig.heartbeatIntervalS) // 未下发的键保持原值
    }

    @Test
    fun resetToDefaults_readsEveryFieldFromConfigDefaults() {
        // 复位是测试之间隔离的基础，而它必须读同一个来源。抽四种类型各验一个：
        // 哪一处写死成字面量，都会在这里露出来。
        RuntimeConfig.apply(
            JSONObject(
                "{\"heartbeat_interval_s\": 45, \"overlay_enabled\": false, " +
                    "\"overlay_message\": \"维护中\", \"bath_app_exit_max_count\": 9}",
            ),
        )
        RuntimeConfig.resetToDefaults()

        assertEquals(ConfigDefaults.HEARTBEAT_INTERVAL_S.toInt(), RuntimeConfig.heartbeatIntervalS)
        assertEquals(ConfigDefaults.OVERLAY_ENABLED, RuntimeConfig.overlayEnabled)
        assertEquals(ConfigDefaults.OVERLAY_MESSAGE, RuntimeConfig.overlayMessage)
        assertEquals(ConfigDefaults.BATH_APP_EXIT_MAX_COUNT.toInt(), RuntimeConfig.bathExitMaxCount)
    }

    @Test
    fun supportedKeys_hasExpectedCount() {
        // 真正的键集合对账在服务端（server/tests/test_config_spec.py，按名字比对）。
        // 这里只守住数量：设备端键凭空变少或变多都该是有意的改动。
        assertEquals(24, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_clampsZeroDurationToAvoidRunawayTimers() {
        // 0 秒心跳会让 postDelayed 立即重发，形成心跳风暴；
        // 服务端已按 spec 拦下越界值，这一层防的是配置经其它路径进来
        RuntimeConfig.apply(JSONObject("{\"heartbeat_interval_s\": 0}"))

        assertEquals(1, RuntimeConfig.heartbeatIntervalS)
    }

    @Test
    fun apply_clampsNegativeDurationToAvoidRunawayTimers() {
        RuntimeConfig.apply(JSONObject("{\"reconnect_base_ms\": -1000, \"ui_poll_interval_ms\": -5}"))

        assertEquals(1L, RuntimeConfig.reconnectBaseMs)
        assertEquals(1L, RuntimeConfig.pollIntervalMs)
    }

    @Test
    fun apply_clampsZeroCountKeys() {
        // 窗口内最多退出 0 次会让守护逻辑判定每次退出都超限
        RuntimeConfig.apply(JSONObject("{\"bath_app_exit_max_count\": 0}"))

        assertEquals(1, RuntimeConfig.bathExitMaxCount)
    }

    @Test
    fun apply_keepsLegitimateValuesUntouched() {
        // 钳制只兜下限，不得把正常值改掉
        RuntimeConfig.apply(
            JSONObject("{\"heartbeat_interval_s\": 45, \"ui_click_delay_ms\": 0, \"bath_app_exit_max_count\": 5}"),
        )

        assertEquals(45, RuntimeConfig.heartbeatIntervalS)
        assertEquals(0L, RuntimeConfig.uiClickDelayMs) // 点击延迟允许为 0
        assertEquals(5, RuntimeConfig.bathExitMaxCount)
    }

    @Test
    fun apply_acceptsAnyJsonTypeForEveryKey() {
        // 服务端下发的值类型异常时（字符串写数字等）沿用该键的默认值，不得抛异常
        RuntimeConfig.supportedKeys.forEach { key ->
            listOf(true, 42, "text", 3.5).forEach { value ->
                RuntimeConfig.apply(JSONObject().put(key, value))
            }
        }
        assertEquals(24, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_doesNotDisturbUnrelatedKeys() {
        RuntimeConfig.apply(JSONObject().put("heartbeat_interval_s", 45))

        assertEquals(10000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(3, RuntimeConfig.bathExitMaxCount)
    }
}
