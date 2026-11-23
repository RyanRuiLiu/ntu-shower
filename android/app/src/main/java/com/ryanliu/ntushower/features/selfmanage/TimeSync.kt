package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
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
 * 这台设备没有 NTP，墙上时钟靠 RTC 维持，时区误设或掉电都会使其偏移（真机
 * 2026-10-02 实测偏差整 1 小时）。MCU 里的开关机截止按布防那一刻的钟面换算，钟面
 * 变化会使截止在真实时间里偏移一个校钟量（见 docs/device-poweroff.md）。因此写钟
 * 必须成对——**先将时钟写准，再按设备本地事实（浴室 App 参数里的平台计划）重布
 * MCU**。写钟时机由服务端决定，本对象只承担写入、验证与配对重布。
 *
 * ## 写时钟的命令（真机验证 2026-10-02，勿改）
 *
 * `date -s "YYYYMMDD.HHMMSS"`（设备本地时区）。这台 4.4 ROM 的 toolbox date：
 * `date -u -s "ISO 格式"` 被静默忽略，BSD 风格 `MMDDhhmmCCYY.ss` 会解析出 2015 年，
 * 两种常见写法均无效；唯一有效的是旧系统（ntu-remote-shower viewer）验证过的形态。
 * 时刻换算在本机日历上完成，不做时区字符串拼接。
 */
object TimeSync {

    private const val TAG = "TimeSync"

    /** 写入后的验收门槛：残余偏差超过该值即视为未写准，按失败回报。 */
    private const val MANUAL_THRESHOLD_MS = 5_000L

    /** 设备本地墙钟时间格式：秒级。 */
    private val DATETIME_SHAPE = Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$""")

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 写系统时钟：[datetime] 为设备本地墙钟时间（YYYY-MM-DD HH:MM:SS）。
     *
     * 手动（设备页「校正时间」）与自动（服务端自动校时）共用这一条写入与验证路径，
     * 执行不受配置开关影响——命令到达即写入，区别仅在触发者。返回是否成功，
     * 结果同时落 device.log。
     *
     * [rearm] 是配对不变式的开关：钟面变动后，MCU 里的开关机截止必须在新钟面下
     * 重写一次，否则真实断电/上电时刻会偏差一个校钟量，目标取自设备本地事实。
     * `rearm=false` 供电源计划的 hold 施加使用——它自行管理布防：一次布防承担整晚
     * （ADR-0025），且布防先于拨钟（写钟前的钟面才是布防换算的依据）。
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
            // 重布目标取设备本地事实（浴室 App 参数里的平台计划），不依赖服务端下发——
            // 写钟与重布的配对不变式在设备侧自洽，服务端离线时同样成立。
            val facts = BathAppFacts.read()
            if (facts == null) {
                Log.w(TAG, "rearm skipped: power facts unavailable")
                deviceLog.error("clock.set-rearm-skipped", "本地电源计划事实缺失，跳过重布")
            } else {
                McuPowerSchedule.schedule(facts.autoCloseTime, facts.autoOpenTime)
            }
        }
        return true
    }

    /**
     * 把墙钟写到 [targetEpochMs]，返回写入后的残余偏差（毫秒）。
     *
     * 流逝量必须取自单调时钟（Uptime）：墙钟刚被改动过，用墙钟计算流逝会将跳变量计入。
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
