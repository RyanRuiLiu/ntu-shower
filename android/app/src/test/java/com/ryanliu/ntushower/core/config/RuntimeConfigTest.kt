package com.ryanliu.ntushower.core.config

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class RuntimeConfigTest {

    @Before
    fun resetDefaults() {
        // 复位读的是 ConfigDefaults（唯一来源）。此前这里是测试内手写的一份默认值副本，
        // 它已经与生成的那份漂移——被 apply_acceptsAnyJsonTypeForEveryKey 污染之后不复位，
        // 测试之间互相干扰。
        RuntimeConfig.resetToDefaults()
    }

    @Test
    fun apply_knownKeysAreApplied() {
        RuntimeConfig.apply(JSONObject("{\"notice_enabled\": true, \"notice_text\": \"测试\"}"))
        assertEquals(true, RuntimeConfig.noticeEnabled)
        assertEquals("测试", RuntimeConfig.noticeText)
    }

    @Test
    fun apply_unknownKeysAreIgnored() {
        // resetDefaults 后为默认值；未知键不影响任何配置项
        RuntimeConfig.apply(JSONObject("{\"future_key\": 1}"))
        assertEquals(28, RuntimeConfig.statusLineTextSizeSp)
    }

    @Test
    fun apply_coversEveryConfigKey() {
        val config = JSONObject(
            "{\"notice_enabled\": true, \"notice_text\": \"维护中\", " +
                "\"notice_text_size_sp\": 30, \"notice_text_color\": \"#112233\", " +
                "\"notice_align\": \"居中\", \"notice_x_dp\": 8, \"notice_y_dp\": 4, " +
                "\"notice_time_enabled\": true, \"notice_time_start\": \"21:30\", " +
                "\"notice_time_end\": \"22:30\", \"notice_weekdays\": \"1,3,5\", " +
                "\"notice_occupancy_enabled\": true, \"notice_occupancy_min\": 3, " +
                "\"notice_occupancy_max\": 12, \"status_line_enabled\": false, " +
                "\"status_line_text_size_sp\": 18, \"status_line_text_color\": \"#223344\", " +
                "\"status_line_available_color\": \"#445566\", " +
                "\"status_line_unavailable_color\": \"#556677\", " +
                "\"status_line_background\": false, \"status_line_background_color\": \"#778899\", " +
                "\"status_line_y_dp\": 24, \"status_line_time_enabled\": true, " +
                "\"status_line_time_start\": \"20:30\", \"status_line_time_end\": \"21:30\", " +
                "\"status_line_weekdays\": \"1,3\", \"status_line_occupancy_enabled\": true, " +
                "\"status_line_occupancy_min\": 2, \"status_line_occupancy_max\": 9, " +
                "\"automation_trace_enabled\": true, \"bath_db_archive_enabled\": false, " +
                "\"power_off_at\": \"23:50\", \"power_on_at\": \"10:00\"}",
        )
        RuntimeConfig.apply(config)

        assertEquals(true, RuntimeConfig.noticeEnabled)
        assertEquals("维护中", RuntimeConfig.noticeText)
        assertEquals(30, RuntimeConfig.noticeTextSizeSp)
        assertEquals("#112233", RuntimeConfig.noticeTextColor)
        assertEquals("居中", RuntimeConfig.noticeAlign)
        assertEquals(8, RuntimeConfig.noticeXDp)
        assertEquals(4, RuntimeConfig.noticeYDp)
        assertEquals(true, RuntimeConfig.noticeTimeEnabled)
        assertEquals("21:30", RuntimeConfig.noticeTimeStart)
        assertEquals("22:30", RuntimeConfig.noticeTimeEnd)
        assertEquals("1,3,5", RuntimeConfig.noticeWeekdays)
        assertEquals(true, RuntimeConfig.noticeOccupancyEnabled)
        assertEquals(3, RuntimeConfig.noticeOccupancyMin)
        assertEquals(12, RuntimeConfig.noticeOccupancyMax)
        assertEquals(false, RuntimeConfig.statusLineEnabled)
        assertEquals(18, RuntimeConfig.statusLineTextSizeSp)
        assertEquals("#223344", RuntimeConfig.statusLineTextColor)
        assertEquals("#445566", RuntimeConfig.statusLineAvailableColor)
        assertEquals("#556677", RuntimeConfig.statusLineUnavailableColor)
        assertEquals(false, RuntimeConfig.statusLineBackground)
        assertEquals("#778899", RuntimeConfig.statusLineBackgroundColor)
        assertEquals(24, RuntimeConfig.statusLineYDp)
        assertEquals(true, RuntimeConfig.statusLineTimeEnabled)
        assertEquals("20:30", RuntimeConfig.statusLineTimeStart)
        assertEquals("21:30", RuntimeConfig.statusLineTimeEnd)
        assertEquals("1,3", RuntimeConfig.statusLineWeekdays)
        assertEquals(true, RuntimeConfig.statusLineOccupancyEnabled)
        assertEquals(2, RuntimeConfig.statusLineOccupancyMin)
        assertEquals(9, RuntimeConfig.statusLineOccupancyMax)
        assertEquals(true, RuntimeConfig.automationTraceEnabled)
        assertEquals(false, RuntimeConfig.bathDbArchiveEnabled)
    }

    @Test
    fun apply_missingKeysKeepCurrentValues() {
        RuntimeConfig.apply(JSONObject("{\"notice_text_size_sp\": 30}"))
        assertEquals(30, RuntimeConfig.noticeTextSizeSp)
    }

    @Test
    fun resetToDefaults_readsEveryFieldFromConfigDefaults() {
        // 复位是测试之间隔离的基础，而它必须读同一个来源。抽四种类型各验一个：
        // 哪一处写死成字面量，都会在这里露出来。
        RuntimeConfig.apply(
            JSONObject(
                "{\"notice_enabled\": true, \"notice_text_size_sp\": 45, " +
                    "\"notice_text\": \"维护中\"}",
            ),
        )
        RuntimeConfig.resetToDefaults()

        assertEquals(ConfigDefaults.NOTICE_ENABLED, RuntimeConfig.noticeEnabled)
        assertEquals(ConfigDefaults.NOTICE_TEXT_SIZE_SP.toInt(), RuntimeConfig.noticeTextSizeSp)
        assertEquals(ConfigDefaults.NOTICE_TEXT, RuntimeConfig.noticeText)
    }

    @Test
    fun supportedKeys_hasExpectedCount() {
        // 真正的键集合对账在服务端（server/tests/test_config_spec.py，按名字比对）。
        // 这里只守住数量：设备端键凭空变少或变多都该是有意的改动。
        assertEquals(35, RuntimeConfig.supportedKeys.size)
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
    fun apply_keepsZeroTextSizeButClampsPositions() {
        // 字号 0 会被抬到 1（"失控下限"），文字对齐无钳制；x/y 左边距/上边距仍非负。
        RuntimeConfig.apply(
            JSONObject()
                .put("notice_text_size_sp", 0)
                .put("notice_x_dp", -120)
                .put("notice_y_dp", -40)
                .put("notice_occupancy_min", 0)
                .put("notice_occupancy_max", 0),
        )

        assertEquals(1, RuntimeConfig.noticeTextSizeSp)
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
                .put("status_line_available_color", "#112233")
                .put("status_line_unavailable_color", "#445566")
                .put("status_line_background", false)
                .put("status_line_background_color", "#778899")
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
        assertEquals("#112233", RuntimeConfig.statusLineAvailableColor)
        assertEquals("#445566", RuntimeConfig.statusLineUnavailableColor)
        assertEquals(false, RuntimeConfig.statusLineBackground)
        assertEquals("#778899", RuntimeConfig.statusLineBackgroundColor)
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
        // 字号 0 会让整行看不见；y 的 0 合法，负数则没有意义。
        RuntimeConfig.apply(
            JSONObject()
                .put("status_line_text_size_sp", 0)
                .put("status_line_y_dp", -8)
                .put("status_line_occupancy_min", -2)
                .put("status_line_occupancy_max", -1),
        )

        assertEquals(1, RuntimeConfig.statusLineTextSizeSp)
        assertEquals(0, RuntimeConfig.statusLineYDp)
        assertEquals(0, RuntimeConfig.statusLineOccupancyMin)
        assertEquals(0, RuntimeConfig.statusLineOccupancyMax)
    }

    @Test
    fun apply_readsBathDbArchiveKeys() {
        RuntimeConfig.apply(JSONObject().put("bath_db_archive_enabled", false))

        assertEquals(false, RuntimeConfig.bathDbArchiveEnabled)
    }

    @Test
    fun apply_clampsZeroTextSizeToAvoidInvisibleText() {
        // 字号 0 会让文字不可见；服务端已按 spec 拦下越界值，这一层防的是配置经其它路径进来
        RuntimeConfig.apply(JSONObject("{\"notice_text_size_sp\": 0}"))

        assertEquals(1, RuntimeConfig.noticeTextSizeSp)
    }

    @Test
    fun apply_keepsLegitimateValuesUntouched() {
        // 钳制只作用于下限，不得把正常值改掉
        RuntimeConfig.apply(
            JSONObject("{\"notice_text_size_sp\": 45, \"notice_y_dp\": 0, \"notice_occupancy_max\": 5}"),
        )

        assertEquals(45, RuntimeConfig.noticeTextSizeSp)
        assertEquals(0, RuntimeConfig.noticeYDp) // y 的 0 是合法值
        assertEquals(5, RuntimeConfig.noticeOccupancyMax)
    }

    @Test
    fun apply_acceptsAnyJsonTypeForEveryKey() {
        // 服务端下发的值类型异常时（字符串写数字等）沿用该键的默认值，不得抛异常
        RuntimeConfig.supportedKeys.forEach { key ->
            listOf(true, 42, "text", 3.5).forEach { value ->
                RuntimeConfig.apply(JSONObject().put(key, value))
            }
        }
        assertEquals(35, RuntimeConfig.supportedKeys.size)
    }

    @Test
    fun apply_doesNotDisturbUnrelatedKeys() {
        RuntimeConfig.apply(JSONObject().put("notice_text_size_sp", 45))

        assertEquals(true, RuntimeConfig.bathDbArchiveEnabled)
    }

    @Test
    fun apply_coversAutomationTimingKeys() {
        // 四个自动化时序键：下发生效、下限收口到 1（0 毫秒的超时/窗口不是合法值）。
        // 逐键断言映射没有错位——键名与属性的对应是纯手写的，错位只有显式断言能抓
        RuntimeConfig.apply(
            JSONObject(
                "{\"command_dedup_max_count\": 512, \"bath_response_timeout_ms\": 8000, " +
                    "\"cert_verify_wait_ms\": 3000, \"home_timeout_ms\": 12000}",
            ),
        )

        assertEquals(512L, RuntimeConfig.commandDedupMaxCount)
        assertEquals(8000L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(3000L, RuntimeConfig.idVerifyWaitMs)
        assertEquals(12000L, RuntimeConfig.homeBudgetMs)

        RuntimeConfig.apply(
            JSONObject(
                "{\"command_dedup_max_count\": 0, \"bath_response_timeout_ms\": -5, " +
                    "\"cert_verify_wait_ms\": 0, \"home_timeout_ms\": -1}",
            ),
        )
        assertEquals(1L, RuntimeConfig.commandDedupMaxCount)
        assertEquals(1L, RuntimeConfig.serverResponseTimeoutMs)
        assertEquals(1L, RuntimeConfig.idVerifyWaitMs)
        assertEquals(1L, RuntimeConfig.homeBudgetMs)
    }
}
