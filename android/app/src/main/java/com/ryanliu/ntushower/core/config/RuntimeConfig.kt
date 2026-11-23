package com.ryanliu.ntushower.core.config

import org.json.JSONObject

/**
 * 运行时配置：服务端 hello_ack / config.update 下发，进程内全局生效。
 *
 * ## 取值下限
 *
 * 服务端按 `spec.CONFIG_SPECS` 做业务范围校验（例如字号 10~200 sp），设备端
 * **不重复该业务范围**——两处各写一份必然漂移。这里只拦截失控取值：0 或负数的时长
 * 会让 `postDelayed` 立即触发（心跳/重连风暴），所以一律抬到至少 1。
 *
 * 每个键在这里各自钳到自己的合法区间：配置可能经手工干预进来，设备作为独立进程
 * 按自己的规则取值，不依赖上游已经做过什么。
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
     * 只对"间隔/超时/窗口/计数"这类键使用。x/y 边距与占用上下限的 0 是有意义的取值
     * （贴边、精确匹配 0 个占用），不经这里，各自只挡负数。
     */
    private const val MIN_POSITIVE = 1L

    private fun atLeastOne(value: Long): Long = value.coerceAtLeast(MIN_POSITIVE)

    private fun atLeastOne(value: Int): Int = value.coerceAtLeast(MIN_POSITIVE.toInt())

    // ── 屏幕提示 ──
    // 配置面在服务端；显隐判定在设备端的 NoticeRule。这里只保存下发值。
    @Volatile var noticeEnabled = ConfigDefaults.NOTICE_ENABLED
        private set

    @Volatile var noticeText = ConfigDefaults.NOTICE_TEXT
        private set

    @Volatile var noticeTextSizeSp = ConfigDefaults.NOTICE_TEXT_SIZE_SP.toInt()
        private set

    @Volatile var noticeTextColor = ConfigDefaults.NOTICE_TEXT_COLOR
        private set

    @Volatile var noticeAlign = ConfigDefaults.NOTICE_ALIGN
        private set

    // x/y 与占用上下限的 0 是合法值，只挡住负数；字号则至少为 1。
    @Volatile var noticeXDp = ConfigDefaults.NOTICE_X_DP.toInt()
        private set

    @Volatile var noticeYDp = ConfigDefaults.NOTICE_Y_DP.toInt()
        private set

    @Volatile var noticeTimeEnabled = ConfigDefaults.NOTICE_TIME_ENABLED
        private set

    @Volatile var noticeTimeStart = ConfigDefaults.NOTICE_TIME_START
        private set

    @Volatile var noticeTimeEnd = ConfigDefaults.NOTICE_TIME_END
        private set

    @Volatile var noticeWeekdays = ConfigDefaults.NOTICE_WEEKDAYS
        private set

    @Volatile var noticeOccupancyEnabled = ConfigDefaults.NOTICE_OCCUPANCY_ENABLED
        private set

    @Volatile var noticeOccupancyMin = ConfigDefaults.NOTICE_OCCUPANCY_MIN.toInt()
        private set

    @Volatile var noticeOccupancyMax = ConfigDefaults.NOTICE_OCCUPANCY_MAX.toInt()
        private set

    // ── 楼层状态行 ──
    // 内容由服务端汇总后经 status_line.update 推送；这里只保存显示样式、位置与条件。
    @Volatile var statusLineEnabled = ConfigDefaults.STATUS_LINE_ENABLED
        private set

    @Volatile var statusLineTextSizeSp = ConfigDefaults.STATUS_LINE_TEXT_SIZE_SP.toInt()
        private set

    @Volatile var statusLineTextColor = ConfigDefaults.STATUS_LINE_TEXT_COLOR
        private set

    @Volatile var statusLineAvailableColor = ConfigDefaults.STATUS_LINE_AVAILABLE_COLOR
        private set

    @Volatile var statusLineUnavailableColor = ConfigDefaults.STATUS_LINE_UNAVAILABLE_COLOR
        private set

    @Volatile var statusLineBackground = ConfigDefaults.STATUS_LINE_BACKGROUND
        private set

    @Volatile var statusLineBackgroundColor = ConfigDefaults.STATUS_LINE_BACKGROUND_COLOR
        private set

    @Volatile var statusLineYDp = ConfigDefaults.STATUS_LINE_Y_DP.toInt()
        private set

    @Volatile var statusLineTimeEnabled = ConfigDefaults.STATUS_LINE_TIME_ENABLED
        private set

    @Volatile var statusLineTimeStart = ConfigDefaults.STATUS_LINE_TIME_START
        private set

    @Volatile var statusLineTimeEnd = ConfigDefaults.STATUS_LINE_TIME_END
        private set

    @Volatile var statusLineWeekdays = ConfigDefaults.STATUS_LINE_WEEKDAYS
        private set

    @Volatile var statusLineOccupancyEnabled = ConfigDefaults.STATUS_LINE_OCCUPANCY_ENABLED
        private set

    @Volatile var statusLineOccupancyMin = ConfigDefaults.STATUS_LINE_OCCUPANCY_MIN.toInt()
        private set

    @Volatile var statusLineOccupancyMax = ConfigDefaults.STATUS_LINE_OCCUPANCY_MAX.toInt()
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

    // ── 浴室 App 数据库 ──
    @Volatile var bathDbArchiveEnabled = ConfigDefaults.BATH_DB_ARCHIVE_ENABLED
        private set

    // ── 电源计划：开关机时刻改由设备本地事实（BathAppFacts）自持，不再是服务端下发键 ──

    /**
     * 把每个键复位为 [ConfigDefaults] 的值。
     *
     * 存在的原因是本对象是进程内的全局单例：一处改过之后，别处读到的不再是默认值，
     * 而测试需要在每个用例开始前拿回确定的状态。
     *
     * **实现为向 [apply] 提供一份默认值**：该默认值由生成器产出（[ConfigDefaults.asJson]），
     * 所以这里不再逐键抄一遍——手抄的那份是与 [ConfigDefaults] 必然分叉的第二个来源，
     * 而且分叉了测试照样通过（它断言的是自己刚设进去的那个值）。
     *
     * 生产代码不走这里：配置一旦下发就生效到进程结束。
     */
    fun resetToDefaults() {
        apply(ConfigDefaults.asJson())
    }

    /**
     * 键 → 应用函数映射表：新增配置键只需加一行（复杂度不随键数增长）。
     *
     * **键名只出现在这一处**：lambda 的第二个参数就是 map 的那个键，取值时用它，不再
     * 手抄一遍。抄两遍的时候敲错一个字母不报错——那一项从此永远读默认值，而
     * `test_config_spec.py` 只查 map 的键，查不出来。
     *
     * 每个时长与计数取值都经 atLeastOne 取下限：见类注释的"取值下限"。
     */
    private val appliers: Map<String, (JSONObject, String) -> Unit> = mapOf(
        "notice_enabled" to { c, key -> noticeEnabled = c.optBoolean(key, noticeEnabled) },
        "notice_text" to { c, key -> noticeText = c.optString(key, noticeText) },
        "notice_text_size_sp" to
            { c, key -> noticeTextSizeSp = atLeastOne(c.optInt(key, noticeTextSizeSp)) },
        "notice_text_color" to { c, key -> noticeTextColor = c.optString(key, noticeTextColor) },
        "notice_align" to { c, key -> noticeAlign = c.optString(key, noticeAlign) },
        "notice_x_dp" to { c, key -> noticeXDp = c.optInt(key, noticeXDp).coerceAtLeast(0) },
        "notice_y_dp" to { c, key -> noticeYDp = c.optInt(key, noticeYDp).coerceAtLeast(0) },
        "notice_time_enabled" to
            { c, key -> noticeTimeEnabled = c.optBoolean(key, noticeTimeEnabled) },
        "notice_time_start" to { c, key -> noticeTimeStart = c.optString(key, noticeTimeStart) },
        "notice_time_end" to { c, key -> noticeTimeEnd = c.optString(key, noticeTimeEnd) },
        "notice_weekdays" to { c, key -> noticeWeekdays = c.optString(key, noticeWeekdays) },
        "notice_occupancy_enabled" to
            { c, key -> noticeOccupancyEnabled = c.optBoolean(key, noticeOccupancyEnabled) },
        "notice_occupancy_min" to
            { c, key -> noticeOccupancyMin = c.optInt(key, noticeOccupancyMin).coerceAtLeast(0) },
        "notice_occupancy_max" to
            { c, key -> noticeOccupancyMax = c.optInt(key, noticeOccupancyMax).coerceAtLeast(0) },
        "status_line_enabled" to
            { c, key -> statusLineEnabled = c.optBoolean(key, statusLineEnabled) },
        "status_line_text_size_sp" to
            { c, key -> statusLineTextSizeSp = atLeastOne(c.optInt(key, statusLineTextSizeSp)) },
        "status_line_text_color" to
            { c, key -> statusLineTextColor = c.optString(key, statusLineTextColor) },
        "status_line_available_color" to
            { c, key -> statusLineAvailableColor = c.optString(key, statusLineAvailableColor) },
        "status_line_unavailable_color" to
            { c, key -> statusLineUnavailableColor = c.optString(key, statusLineUnavailableColor) },
        "status_line_background" to
            { c, key -> statusLineBackground = c.optBoolean(key, statusLineBackground) },
        "status_line_background_color" to
            { c, key -> statusLineBackgroundColor = c.optString(key, statusLineBackgroundColor) },
        "status_line_y_dp" to
            { c, key -> statusLineYDp = c.optInt(key, statusLineYDp).coerceAtLeast(0) },
        "status_line_time_enabled" to
            { c, key -> statusLineTimeEnabled = c.optBoolean(key, statusLineTimeEnabled) },
        "status_line_time_start" to
            { c, key -> statusLineTimeStart = c.optString(key, statusLineTimeStart) },
        "status_line_time_end" to
            { c, key -> statusLineTimeEnd = c.optString(key, statusLineTimeEnd) },
        "status_line_weekdays" to
            { c, key -> statusLineWeekdays = c.optString(key, statusLineWeekdays) },
        "status_line_occupancy_enabled" to
            { c, key -> statusLineOccupancyEnabled = c.optBoolean(key, statusLineOccupancyEnabled) },
        "status_line_occupancy_min" to
            { c, key -> statusLineOccupancyMin = c.optInt(key, statusLineOccupancyMin).coerceAtLeast(0) },
        "status_line_occupancy_max" to
            { c, key -> statusLineOccupancyMax = c.optInt(key, statusLineOccupancyMax).coerceAtLeast(0) },
        "automation_trace_enabled" to
            { c, key -> automationTraceEnabled = c.optBoolean(key, automationTraceEnabled) },
        "command_dedup_max_count" to
            { c, key -> commandDedupMaxCount = atLeastOne(c.optLong(key, commandDedupMaxCount)) },
        "bath_response_timeout_ms" to
            { c, key -> serverResponseTimeoutMs = atLeastOne(c.optLong(key, serverResponseTimeoutMs)) },
        "cert_verify_wait_ms" to
            { c, key -> idVerifyWaitMs = atLeastOne(c.optLong(key, idVerifyWaitMs)) },
        "home_timeout_ms" to
            { c, key -> homeBudgetMs = atLeastOne(c.optLong(key, homeBudgetMs)) },
        "bath_db_archive_enabled" to
            { c, key -> bathDbArchiveEnabled = c.optBoolean(key, bathDbArchiveEnabled) },
    )

    /** 应用下发的配置；未知键忽略（服务端可能比设备端新，新增键由后续版本消化）。 */
    fun apply(config: JSONObject) {
        val keys = config.keys()
        while (keys.hasNext()) {
            val key = keys.next()
            appliers[key]?.invoke(config, key)
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
