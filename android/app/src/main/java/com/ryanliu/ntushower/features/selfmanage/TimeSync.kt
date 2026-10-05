package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.clock.Clock
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
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 开机时间自校正：以 `link.hello_ack` 的 `server_time` 为准把系统时钟写对，再**直写
 * MCU 重布定时开关机**。
 *
 * 这台设备没有 NTP，墙上时钟靠 RTC 维持，时区误设或掉电都会跑偏（真机 2026-10-02
 * 实测快了整 1 小时）。MCU 里的开关机截止是按布防那一刻的钟面换算的，钟一动截止就
 * 跟着在真实时间里漂（见 docs/device-poweroff.md §11）。因此校正必须成对——**先把
 * 时钟写对，再按服务端下发的电源计划（power_off_at / power_on_at）重布 MCU**；目标
 * 由服务端全权控制，不再依赖重启浴室 App 让它"重读"什么。
 *
 * ## 开关与阈值
 *
 * 功能默认**关闭**，由设备配置项 `boot_time_sync_enabled` 控制（配置页「时间校正」）；
 * 触发阈值为 `boot_time_sync_threshold_s`（默认 60 秒，无上限）：偏差超过阈值才校正
 * 并直写 MCU 重布定时开关机，阈值内的秒级噪声不动。手动设置（setWallClock）是显式动作，
 * 不受这两个配置影响。
 *
 * ## 写时钟的命令（真机验证 2026-10-02，勿改）
 *
 * `date -s "YYYYMMDD.HHMMSS"`（设备本地时区）。这台 4.4 ROM 的 toolbox date：
 * `date -u -s "ISO 格式"` 被静默忽略，BSD 风格 `MMDDhhmmCCYY.ss` 会解析出 2015 年，
 * 两种常见写法都不行；唯一有效的是旧系统（ntu-remote-shower viewer）验证过的形态。
 * 时刻换算在本机日历上做，不做时区字符串拼接。
 *
 * ## 每次开机一次
 *
 * [syncFrom] 以原子闸门保证开机后只评估一次（无论结果如何）——断线重连触发的后续
 * hello_ack 不再重复。校正成功后，服务端基于 link.pong 的时钟偏差测量随之归零。
 */
object TimeSync {

    private const val TAG = "TimeSync"

    /** 手动设置（网页端）的验证残余门槛：显式动作允许比开机校时更紧的判定。 */
    private const val MANUAL_THRESHOLD_MS = 5_000L

    /** 每次开机只尝试一次：重启、网络闪断引发的重连都由这道闸门挡住。 */
    private val attempted = AtomicBoolean(false)

    /** 网页端手动设置的时间格式：设备本地墙钟，秒级。 */
    private val DATETIME_SHAPE = Regex("""^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$""")

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /** 开机校正入口：GatewayClient 收到 hello_ack 后在工作线程调用。 */
    fun syncFrom(serverTimeRfc3339: String) {
        if (!attempted.compareAndSet(false, true)) return
        if (!RuntimeConfig.bootTimeSyncEnabled) {
            Log.i(TAG, "boot time sync disabled by config")
            deviceLog.lifecycle("clock.sync-disabled", "配置未开启")
            return
        }
        val server = Clock.parseRfc3339(serverTimeRfc3339)
        if (server == null) {
            Log.w(TAG, "cannot parse server time: $serverTimeRfc3339")
            deviceLog.error("clock.sync-failed", "无法解析服务端时间")
            return
        }

        // 流逝量一律走单调时钟（Uptime）：墙钟即将跳变，用墙钟算流逝会把跳变量算进结果
        val upBefore = Uptime.nowMs()
        val offsetMs = server.time - System.currentTimeMillis()
        val thresholdMs = RuntimeConfig.bootTimeSyncThresholdS * 1000
        if (!needsCorrection(offsetMs, thresholdMs)) {
            Log.i(TAG, "clock aligned (offset=${offsetMs}ms, threshold=${thresholdMs}ms)")
            deviceLog.lifecycle("clock.aligned", "offset=${offsetMs}ms, threshold=${thresholdMs}ms")
            return
        }

        deviceLog.lifecycle("clock.correcting", "offset=${offsetMs}ms")
        val residual = applyWallClock(server.time)
        if (needsCorrection(residual, thresholdMs)) {
            Log.e(TAG, "clock correction failed (residual=${residual}ms)")
            deviceLog.error("clock.correct-failed", "residual=${residual}ms")
            return
        }
        Log.i(TAG, "clock corrected (residual=${residual}ms), rearming mcu power schedule")
        deviceLog.lifecycle("clock.corrected", "residual=${residual}ms")
        // 目标取自下发的电源计划：开关机时刻由服务端配置全权决定（校钟之后截止随钟面
        // 移动，必须在校正后的框架里重写一次，否则真实断电时刻会偏差一个校钟量）。
        McuPowerSchedule.schedule(RuntimeConfig.powerOffAt, RuntimeConfig.powerOnAt)
    }

    /**
     * 手动设置系统时钟（网页端「校正时间」）：[datetime] 为设备本地墙钟时间
     * （YYYY-MM-DD HH:MM:SS）。与开机校正共用写入与验证路径；返回是否成功，
     * 结果同时落 device.log。
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
        // 配对不变式：钟面框架一动，MCU 里的开关机截止必须在新框架里重写一次，
        // 否则真实断电/上电时刻会偏差一个校钟量。目标取自下发的电源计划。
        // rearm=false 供延长服务流程内部使用——它自己管理 MCU 的滚动重布。
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

    /** 手动设置后的残余偏差判定：显式动作用固定的小门槛，不受开机校时配置影响。 */
    private fun needsCorrection(offsetMs: Long): Boolean = Math.abs(offsetMs) >= MANUAL_THRESHOLD_MS

    /** 开机校时的偏差判定：阈值来自远程配置。单独提出便于单测锁定语义。 */
    internal fun needsCorrection(offsetMs: Long, thresholdMs: Long): Boolean = Math.abs(offsetMs) >= thresholdMs

    /** 服务端时刻 → 本 ROM `date -s` 认的 `YYYYMMDD.HHMMSS`（按 [zone] 的本地墙钟）。 */
    internal fun shellDatetime(epochMs: Long, zone: TimeZone = TimeZone.getDefault()): String {
        val format = SimpleDateFormat("yyyyMMdd.HHmmss", Locale.US).apply { timeZone = zone }
        return format.format(Date(epochMs))
    }
}
