package com.ryanliu.ntushower.core.config

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

    /** 等待服务端响应 */
    const val BATH_RESPONSE_TIMEOUT_MS = 10000L

    /** 等待身份校验 */
    const val CERT_VERIFY_WAIT_MS = 5000L

    /** 回首页预算 */
    const val HOME_TIMEOUT_MS = 10000L

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
}
