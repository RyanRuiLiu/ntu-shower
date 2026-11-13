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
        assertEquals(64, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_readsNoticeKeys() {
        RuntimeConfig.apply(
            JSONObject()
                .put("notice_enabled", true)
                .put("notice_text", "测试提示")
                .put("notice_text_size_sp", 60)
                .put("notice_text_color", "#112233")
                .put("notice_align", "居中")
                .put("notice_center_offset_dp", 60)
                .put("notice_x_dp", 120)
                .put("notice_y_dp", 240)
                .put("notice_time_enabled", true)
                .put("notice_time_start", "21:30")
                .put("notice_time_end", "22:30")
                .put("notice_weekdays", "1,3,5")
                .put("notice_occupancy_enabled", true)
                .put("notice_occupancy_min", 3)
                .put("notice_occupancy_max", 12),
        )

        assertEquals(true, RuntimeConfig.noticeEnabled)
        assertEquals("测试提示", RuntimeConfig.noticeText)
        assertEquals(60, RuntimeConfig.noticeTextSizeSp)
        assertEquals("#112233", RuntimeConfig.noticeTextColor)
        assertEquals("居中", RuntimeConfig.noticeAlign)
        assertEquals(60, RuntimeConfig.noticeCenterOffsetDp)
        assertEquals(120, RuntimeConfig.noticeXDp)
        assertEquals(240, RuntimeConfig.noticeYDp)
        assertEquals(true, RuntimeConfig.noticeTimeEnabled)
        assertEquals("21:30", RuntimeConfig.noticeTimeStart)
        assertEquals("22:30", RuntimeConfig.noticeTimeEnd)
        assertEquals("1,3,5", RuntimeConfig.noticeWeekdays)
        assertEquals(true, RuntimeConfig.noticeOccupancyEnabled)
        assertEquals(3, RuntimeConfig.noticeOccupancyMin)
        assertEquals(12, RuntimeConfig.noticeOccupancyMax)
    }

    @Test
    fun apply_keepsNegativeCenterOffsetButClampsPositions() {
        // 字号 0 会让文字不可见；居中偏移可为负（向左），而 x/y 左边距/上边距仍非负。
        RuntimeConfig.apply(
            JSONObject()
                .put("notice_text_size_sp", 0)
                .put("notice_center_offset_dp", -40)
                .put("notice_x_dp", -120)
                .put("notice_y_dp", -40)
                .put("notice_occupancy_min", 0)
                .put("notice_occupancy_max", 0),
        )

        assertEquals(1, RuntimeConfig.noticeTextSizeSp)
        assertEquals(-40, RuntimeConfig.noticeCenterOffsetDp)
        assertEquals(0, RuntimeConfig.noticeXDp)
        assertEquals(0, RuntimeConfig.noticeYDp)
        assertEquals(0, RuntimeConfig.noticeOccupancyMin)
        assertEquals(0, RuntimeConfig.noticeOccupancyMax)
    }

    @Test
    fun apply_readsStatusLineKeys() {
        RuntimeConfig.apply(
            JSONObject()
                .put("status_line_enabled", false)
                .put("status_line_text_size_sp", 18)
                .put("status_line_text_color", "#223344")
                .put("status_line_color_mode", "统一")
                .put("status_line_available_color", "#112233")
                .put("status_line_unavailable_color", "#445566")
                .put("status_line_background", false)
                .put("status_line_background_color", "#778899")
                .put("status_line_align", "靠右")
                .put("status_line_center_offset_dp", -20)
                .put("status_line_x_dp", 60)
                .put("status_line_y_dp", 24)
                .put("status_line_time_enabled", true)
                .put("status_line_time_start", "21:30")
                .put("status_line_time_end", "22:30")
                .put("status_line_weekdays", "1,3,5")
                .put("status_line_occupancy_enabled", true)
                .put("status_line_occupancy_min", 2)
                .put("status_line_occupancy_max", 9),
        )

        assertEquals(false, RuntimeConfig.statusLineEnabled)
        assertEquals(18, RuntimeConfig.statusLineTextSizeSp)
        assertEquals("#223344", RuntimeConfig.statusLineTextColor)
        assertEquals("统一", RuntimeConfig.statusLineColorMode)
        assertEquals("#112233", RuntimeConfig.statusLineAvailableColor)
        assertEquals("#445566", RuntimeConfig.statusLineUnavailableColor)
        assertEquals(false, RuntimeConfig.statusLineBackground)
        assertEquals("#778899", RuntimeConfig.statusLineBackgroundColor)
        assertEquals("靠右", RuntimeConfig.statusLineAlign)
        assertEquals(-20, RuntimeConfig.statusLineCenterOffsetDp)
        assertEquals(60, RuntimeConfig.statusLineXDp)
        assertEquals(24, RuntimeConfig.statusLineYDp)
        assertEquals(true, RuntimeConfig.statusLineTimeEnabled)
        assertEquals("21:30", RuntimeConfig.statusLineTimeStart)
        assertEquals("22:30", RuntimeConfig.statusLineTimeEnd)
        assertEquals("1,3,5", RuntimeConfig.statusLineWeekdays)
        assertEquals(true, RuntimeConfig.statusLineOccupancyEnabled)
        assertEquals(2, RuntimeConfig.statusLineOccupancyMin)
        assertEquals(9, RuntimeConfig.statusLineOccupancyMax)
    }

    @Test
    fun apply_clampsStatusLineGeometry() {
        // 字号 0 会让整行看不见；x/y 的 0 合法，负数则没有意义。
        RuntimeConfig.apply(
            JSONObject()
                .put("status_line_text_size_sp", 0)
                .put("status_line_center_offset_dp", -40)
                .put("status_line_x_dp", -60)
                .put("status_line_y_dp", -8)
                .put("status_line_occupancy_min", -2)
                .put("status_line_occupancy_max", -1),
        )

        assertEquals(1, RuntimeConfig.statusLineTextSizeSp)
        assertEquals(-40, RuntimeConfig.statusLineCenterOffsetDp)
        assertEquals(0, RuntimeConfig.statusLineXDp)
        assertEquals(0, RuntimeConfig.statusLineYDp)
        assertEquals(0, RuntimeConfig.statusLineOccupancyMin)
        assertEquals(0, RuntimeConfig.statusLineOccupancyMax)
    }

    @Test
    fun apply_readsBathDbArchiveKeys() {
        RuntimeConfig.apply(
            JSONObject().put("bath_db_archive_enabled", true).put("bath_db_archive_threshold_mb", 50),
        )

        assertEquals(true, RuntimeConfig.bathDbArchiveEnabled)
        assertEquals(50L, RuntimeConfig.bathDbArchiveThresholdMb)
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
        assertEquals(64, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_doesNotDisturbUnrelatedKeys() {
        RuntimeConfig.apply(JSONObject().put("heartbeat_interval_s", 45))

        assertEquals(10000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(3, RuntimeConfig.bathExitMaxCount)
    }
}
