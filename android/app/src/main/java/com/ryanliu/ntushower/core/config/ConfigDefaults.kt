package com.ryanliu.ntushower.core.config

import org.json.JSONObject

/**
 * 远程配置的默认值。
 *
 * **本文件由 tools/sync_contracts.py 生成，不要手改**——改了会在下一轮生成时被覆盖，
 * 而 `tests/test_contract_sync.py` 也会立刻失败。要改默认值，改服务端的
 * `app/modules/config/spec.py` 后重新生成。
 *
 * **默认值只有这一处**（它的上游是 spec.py）。设备端不另写一份：两份手写的默认值
 * 必然在某次改动后分叉，而分叉的表现是「行为取决于配置有没有下发到」——
 * 启动后的头几百毫秒里它一定没到，于是表现为偶发的、难以复现的行为差异。
 */
object ConfigDefaults {
    /** 显示屏幕提示 */
    const val NOTICE_ENABLED = false

    /** 提示文字 */
    const val NOTICE_TEXT = ""

    /** 提示字号 */
    const val NOTICE_TEXT_SIZE_SP = 24L

    /** 提示文字颜色 */
    const val NOTICE_TEXT_COLOR = "#de523f"

    /** 提示水平对齐 */
    const val NOTICE_ALIGN = "靠右"

    /** 提示水平边距 */
    const val NOTICE_X_DP = 10L

    /** 提示上边距 */
    const val NOTICE_Y_DP = 6L

    /** 启用时间条件 */
    const val NOTICE_TIME_ENABLED = false

    /** 提示时段开始 */
    const val NOTICE_TIME_START = "22:00"

    /** 提示时段结束 */
    const val NOTICE_TIME_END = "23:00"

    /** 提示生效星期 */
    const val NOTICE_WEEKDAYS = "1,2,3,4,5,6,7"

    /** 启用占用条件 */
    const val NOTICE_OCCUPANCY_ENABLED = false

    /** 占用喷头数下限 */
    const val NOTICE_OCCUPANCY_MIN = 0L

    /** 占用喷头数上限 */
    const val NOTICE_OCCUPANCY_MAX = 48L

    /** 显示楼层状态行 */
    const val STATUS_LINE_ENABLED = true

    /** 状态行字号 */
    const val STATUS_LINE_TEXT_SIZE_SP = 28L

    /** 状态行文字颜色 */
    const val STATUS_LINE_TEXT_COLOR = "#00cc66"

    /** 空闲楼层颜色 */
    const val STATUS_LINE_AVAILABLE_COLOR = "#00cc66"

    /** 不可用楼层颜色 */
    const val STATUS_LINE_UNAVAILABLE_COLOR = "#999999"

    /** 显示状态行背景 */
    const val STATUS_LINE_BACKGROUND = false

    /** 状态行背景颜色 */
    const val STATUS_LINE_BACKGROUND_COLOR = "#110066cc"

    /** 状态行上边距 */
    const val STATUS_LINE_Y_DP = 0L

    /** 启用时间条件 */
    const val STATUS_LINE_TIME_ENABLED = false

    /** 状态行时段开始 */
    const val STATUS_LINE_TIME_START = "22:00"

    /** 状态行时段结束 */
    const val STATUS_LINE_TIME_END = "23:00"

    /** 状态行生效星期 */
    const val STATUS_LINE_WEEKDAYS = "1,2,3,4,5,6,7"

    /** 启用占用条件 */
    const val STATUS_LINE_OCCUPANCY_ENABLED = false

    /** 占用喷头数下限 */
    const val STATUS_LINE_OCCUPANCY_MIN = 0L

    /** 占用喷头数上限 */
    const val STATUS_LINE_OCCUPANCY_MAX = 48L

    /** 记录自动化耗时 */
    const val AUTOMATION_TRACE_ENABLED = false

    /** 命令去重窗口 */
    const val COMMAND_DEDUP_MAX_COUNT = 1024L

    /** 等待服务端响应 */
    const val BATH_RESPONSE_TIMEOUT_MS = 10000L

    /** 等待身份校验 */
    const val CERT_VERIFY_WAIT_MS = 5000L

    /** 回首页预算 */
    const val HOME_TIMEOUT_MS = 15000L

    /** 启用数据库归档 */
    const val BATH_DB_ARCHIVE_ENABLED = true

    /** 每日断电时刻 */
    const val POWER_OFF_AT = "23:58"

    /** 每日上电时刻 */
    const val POWER_ON_AT = "10:55"

    /** 全部默认值拼成一个对象，供 `RuntimeConfig.resetToDefaults` 复位用。 */
    fun asJson(): JSONObject = JSONObject()
        .put("notice_enabled", NOTICE_ENABLED)
        .put("notice_text", NOTICE_TEXT)
        .put("notice_text_size_sp", NOTICE_TEXT_SIZE_SP)
        .put("notice_text_color", NOTICE_TEXT_COLOR)
        .put("notice_align", NOTICE_ALIGN)
        .put("notice_x_dp", NOTICE_X_DP)
        .put("notice_y_dp", NOTICE_Y_DP)
        .put("notice_time_enabled", NOTICE_TIME_ENABLED)
        .put("notice_time_start", NOTICE_TIME_START)
        .put("notice_time_end", NOTICE_TIME_END)
        .put("notice_weekdays", NOTICE_WEEKDAYS)
        .put("notice_occupancy_enabled", NOTICE_OCCUPANCY_ENABLED)
        .put("notice_occupancy_min", NOTICE_OCCUPANCY_MIN)
        .put("notice_occupancy_max", NOTICE_OCCUPANCY_MAX)
        .put("status_line_enabled", STATUS_LINE_ENABLED)
        .put("status_line_text_size_sp", STATUS_LINE_TEXT_SIZE_SP)
        .put("status_line_text_color", STATUS_LINE_TEXT_COLOR)
        .put("status_line_available_color", STATUS_LINE_AVAILABLE_COLOR)
        .put("status_line_unavailable_color", STATUS_LINE_UNAVAILABLE_COLOR)
        .put("status_line_background", STATUS_LINE_BACKGROUND)
        .put("status_line_background_color", STATUS_LINE_BACKGROUND_COLOR)
        .put("status_line_y_dp", STATUS_LINE_Y_DP)
        .put("status_line_time_enabled", STATUS_LINE_TIME_ENABLED)
        .put("status_line_time_start", STATUS_LINE_TIME_START)
        .put("status_line_time_end", STATUS_LINE_TIME_END)
        .put("status_line_weekdays", STATUS_LINE_WEEKDAYS)
        .put("status_line_occupancy_enabled", STATUS_LINE_OCCUPANCY_ENABLED)
        .put("status_line_occupancy_min", STATUS_LINE_OCCUPANCY_MIN)
        .put("status_line_occupancy_max", STATUS_LINE_OCCUPANCY_MAX)
        .put("automation_trace_enabled", AUTOMATION_TRACE_ENABLED)
        .put("command_dedup_max_count", COMMAND_DEDUP_MAX_COUNT)
        .put("bath_response_timeout_ms", BATH_RESPONSE_TIMEOUT_MS)
        .put("cert_verify_wait_ms", CERT_VERIFY_WAIT_MS)
        .put("home_timeout_ms", HOME_TIMEOUT_MS)
        .put("bath_db_archive_enabled", BATH_DB_ARCHIVE_ENABLED)
        .put("power_off_at", POWER_OFF_AT)
        .put("power_on_at", POWER_ON_AT)
}
