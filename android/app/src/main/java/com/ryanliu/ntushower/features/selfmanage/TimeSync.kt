package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.shell.RootShell
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 设备时钟的写入执行者：网页端「校正时间」与服务端的自动校时（`time_sync_enabled`）
 * 都经 `clock.set` 落到 [setWallClock]。
 *
 * 这台设备没有 NTP，墙上时钟靠 RTC 维持，时区误设或掉电都会跑偏（真机 2026-10-02
 * 实测快了整 1 小时）。MCU 里的开关机截止是按布防那一刻的钟面换算的，钟一动截止就
 * 跟着在真实时间里漂（见 docs/device-poweroff.md）。因此每次写钟都必须成对——
 * **先把时钟写对，再按服务端下发的电源计划（power_off_at / power_on_at）重布 MCU**。
 * 何时写钟由服务端决定，这里只把「写」这一件事做对：写入、验证、配对重布。
 *
 * ## 写时钟的命令（真机验证 2026-10-02，勿改）
 *
 * `date -s "YYYYMMDD.HHMMSS"`（设备本地时区）。这台 4.4 ROM 的 toolbox date：
 * `date -u -s "ISO 格式"` 被静默忽略，BSD 风格 `MMDDhhmmCCYY.ss` 会解析出 2015 年，
 * 两种常见写法都不行；唯一有效的是旧系统（ntu-remote-shower viewer）验证过的形态。
 * 时刻换算在本机日历上做，不做时区字符串拼接。
 */
object TimeSync {

    private const val TAG = "TimeSync"

    /** 写完的验收门槛：残余偏差超过它就算没写准，按失败回报。 */
    private const val MANUAL_THRESHOLD_MS = 5_000L

    /** 设备本地墙钟时间格式：秒级。 */
    private val DATETIME_SHAPE = Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$""")

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 写系统时钟：[datetime] 为设备本地墙钟时间（YYYY-MM-DD HH:MM:SS）。
     *
     * 手动（设备页「校正时间」）与自动（服务端自动校时）共用这一条写入与验证路径，
     * 执行不受任何配置开关影响——命令到了就写，区别只在触发者。返回是否成功，
     * 结果同时落 device.log。
     *
     * [rearm] 是配对不变式的开关：钟面框架一动，MCU 里的开关机截止必须在新框架里
     * 重写一次，否则真实断电/上电时刻会偏差一个校钟量，目标取自下发的电源计划。
     * `rearm=false` 供延长服务流程内部使用——它自己管理 MCU 的滚动重布。
     */
    fun setWallClock(datetime: String, rearm: Boolean = true): Boolean {
        if (!DATETIME_SHAPE.matches(datetime)) {
            Log.w(TAG, "rejected malformed datetime: $datetime")
            deviceLog.error("clock.set-failed", "格式不符: $datetime")
            return false
        }
        val target = localDatetimeToEpochMs(datetime)
        if (target == null) {
            Log.w(TAG, "unresolvable datetime: $datetime")
            deviceLog.error("clock.set-failed", "无法解析: $datetime")
            return false
        }
        deviceLog.lifecycle("clock.set-started", "datetime=$datetime")
        val residual = applyWallClock(target)
        val ok = !needsCorrection(residual)
        if (ok) {
            Log.i(TAG, "wall clock set (residual=${residual}ms)")
            deviceLog.lifecycle("clock.set", "residual=${residual}ms")
        } else {
            Log.e(TAG, "wall clock set failed (residual=${residual}ms)")
            deviceLog.error("clock.set-failed", "residual=${residual}ms")
            return false
        }
        if (rearm) {
            McuPowerSchedule.schedule(RuntimeConfig.powerOffAt, RuntimeConfig.powerOnAt)
        }
        return true
    }

    /**
     * 把墙钟写到 [targetEpochMs]，返回校正后的残余偏差（毫秒）。
     *
     * 流逝量必须走单调时钟（Uptime）：墙钟刚跳过一次，用墙钟算流逝会把跳变量算进去。
     */
    private fun applyWallClock(targetEpochMs: Long): Long {
        val upBefore = Uptime.nowMs()
        RootShell.control.exec("date -s ${shellDatetime(targetEpochMs)}")
        val elapsed = Uptime.nowMs() - upBefore
        return System.currentTimeMillis() - (targetEpochMs + elapsed)
    }

    /** 设备本地墙钟时间（秒级）→ epoch 毫秒；解析失败返回 null。 */
    private fun localDatetimeToEpochMs(datetime: String): Long? = try {
        SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(datetime)?.time
    } catch (e: Exception) {
        null
    }

    private fun needsCorrection(offsetMs: Long): Boolean = Math.abs(offsetMs) >= MANUAL_THRESHOLD_MS

    /** 服务端时刻 → 本 ROM `date -s` 认的 `YYYYMMDD.HHMMSS`（按 [zone] 的本地墙钟）。 */
    internal fun shellDatetime(epochMs: Long, zone: TimeZone = TimeZone.getDefault()): String {
        val format = SimpleDateFormat("yyyyMMdd.HHmmss", Locale.US).apply { timeZone = zone }
        return format.format(Date(epochMs))
    }
}
