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
    /** 心跳间隔 */
    const val HEARTBEAT_INTERVAL_S = 30L

    /** 重连退避基准 */
    const val RECONNECT_BASE_MS = 5000L

    /** 重连退避上限 */
    const val RECONNECT_MAX_MS = 60000L

    /** 看门狗间隔 */
    const val WATCHDOG_INTERVAL_S = 60L

    /** 上报去抖窗口 */
    const val DOOR_REPORT_DEBOUNCE_MS = 1000L

    /** 发件箱行数上限 */
    const val OUTBOX_MAX_ROWS = 100000L

    /** 显示操作遮罩 */
    const val OVERLAY_ENABLED = true

    /** 操作遮罩文字 */
    const val OVERLAY_MESSAGE = "网络连接中，请稍后重试..."

    /** 遮罩文字字号 */
    const val OVERLAY_TEXT_SIZE_SP = 80L

    /** 遮罩文字颜色 */
    const val OVERLAY_TEXT_COLOR = "#d94f16"

    /** 遮罩文字左边距 */
    const val OVERLAY_MARGIN_LEFT_DP = 600L

    /** 显示屏幕提示 */
    const val NOTICE_ENABLED = false

    /** 提示文字 */
    const val NOTICE_TEXT = ""

    /** 提示字号 */
    const val NOTICE_TEXT_SIZE_SP = 48L

    /** 提示文字颜色 */
    const val NOTICE_TEXT_COLOR = "#d94f16"

    /** 提示左边距 */
    const val NOTICE_X_DP = 40L

    /** 提示上边距 */
    const val NOTICE_Y_DP = 40L

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

    /** 界面轮询间隔 */
    const val UI_POLL_INTERVAL_MS = 60L

    /** 点击后延迟 */
    const val UI_CLICK_DELAY_MS = 200L

    /** 节点导出上限 */
    const val UI_DUMP_MAX_NODES = 2000L

    /** 滑动默认时长 */
    const val UI_SWIPE_DURATION_MS = 300L

    /** 命令输出上限 */
    const val DEVICE_EXEC_MAX_OUTPUT_BYTES = 32768L

    /** 检查更新间隔 */
    const val OTA_CHECK_INTERVAL_S = 3600L

    /** 崩溃后拉起浴室 App */
    const val BATH_APP_GUARD_ENABLED = true

    /** 退出计数窗口 */
    const val BATH_APP_EXIT_WINDOW_MS = 30000L

    /** 窗口内最多退出次数 */
    const val BATH_APP_EXIT_MAX_COUNT = 3L

    /** 异常后的冷却时长 */
    const val BATH_APP_EXIT_COOLDOWN_MS = 600000L

    /** 启用数据库归档 */
    const val BATH_DB_ARCHIVE_ENABLED = false

    /** 归档触发阈值 */
    const val BATH_DB_ARCHIVE_THRESHOLD_MB = 10L

    /** 每日断电时刻 */
    const val POWER_OFF_AT = "23:58"

    /** 每日上电时刻 */
    const val POWER_ON_AT = "10:55"

    /** 全部默认值拼成一个对象，供 `RuntimeConfig.resetToDefaults` 复位用。 */
    fun asJson(): JSONObject = JSONObject()
        .put("heartbeat_interval_s", HEARTBEAT_INTERVAL_S)
        .put("reconnect_base_ms", RECONNECT_BASE_MS)
        .put("reconnect_max_ms", RECONNECT_MAX_MS)
        .put("watchdog_interval_s", WATCHDOG_INTERVAL_S)
        .put("door_report_debounce_ms", DOOR_REPORT_DEBOUNCE_MS)
        .put("outbox_max_rows", OUTBOX_MAX_ROWS)
        .put("overlay_enabled", OVERLAY_ENABLED)
        .put("overlay_message", OVERLAY_MESSAGE)
        .put("overlay_text_size_sp", OVERLAY_TEXT_SIZE_SP)
        .put("overlay_text_color", OVERLAY_TEXT_COLOR)
        .put("overlay_margin_left_dp", OVERLAY_MARGIN_LEFT_DP)
        .put("notice_enabled", NOTICE_ENABLED)
        .put("notice_text", NOTICE_TEXT)
        .put("notice_text_size_sp", NOTICE_TEXT_SIZE_SP)
        .put("notice_text_color", NOTICE_TEXT_COLOR)
        .put("notice_x_dp", NOTICE_X_DP)
        .put("notice_y_dp", NOTICE_Y_DP)
        .put("notice_time_enabled", NOTICE_TIME_ENABLED)
        .put("notice_time_start", NOTICE_TIME_START)
        .put("notice_time_end", NOTICE_TIME_END)
        .put("notice_weekdays", NOTICE_WEEKDAYS)
        .put("notice_occupancy_enabled", NOTICE_OCCUPANCY_ENABLED)
        .put("notice_occupancy_min", NOTICE_OCCUPANCY_MIN)
        .put("notice_occupancy_max", NOTICE_OCCUPANCY_MAX)
        .put("automation_trace_enabled", AUTOMATION_TRACE_ENABLED)
        .put("command_dedup_max_count", COMMAND_DEDUP_MAX_COUNT)
        .put("bath_response_timeout_ms", BATH_RESPONSE_TIMEOUT_MS)
        .put("cert_verify_wait_ms", CERT_VERIFY_WAIT_MS)
        .put("home_timeout_ms", HOME_TIMEOUT_MS)
        .put("ui_poll_interval_ms", UI_POLL_INTERVAL_MS)
        .put("ui_click_delay_ms", UI_CLICK_DELAY_MS)
        .put("ui_dump_max_nodes", UI_DUMP_MAX_NODES)
        .put("ui_swipe_duration_ms", UI_SWIPE_DURATION_MS)
        .put("device_exec_max_output_bytes", DEVICE_EXEC_MAX_OUTPUT_BYTES)
        .put("ota_check_interval_s", OTA_CHECK_INTERVAL_S)
        .put("bath_app_guard_enabled", BATH_APP_GUARD_ENABLED)
        .put("bath_app_exit_window_ms", BATH_APP_EXIT_WINDOW_MS)
        .put("bath_app_exit_max_count", BATH_APP_EXIT_MAX_COUNT)
        .put("bath_app_exit_cooldown_ms", BATH_APP_EXIT_COOLDOWN_MS)
        .put("bath_db_archive_enabled", BATH_DB_ARCHIVE_ENABLED)
        .put("bath_db_archive_threshold_mb", BATH_DB_ARCHIVE_THRESHOLD_MB)
        .put("power_off_at", POWER_OFF_AT)
        .put("power_on_at", POWER_ON_AT)
}
