package com.ryanliu.ntushower.features.overlay

import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.ServerClock
import java.util.Calendar
import java.util.TimeZone

/**
 * 服务端时刻换算后的本地日历读数。
 *
 * `previousIsoWeekday` 只服务跨午夜时段：00:30 属于前一天开始的窗口时，星期要按
 * 前一天算。
 */
data class LocalMoment(val isoWeekday: Int, val previousIsoWeekday: Int, val minuteOfDay: Int)

/**
 * 浮层条件用的时间口径：服务端时间轴 + 设备系统时区。
 *
 * 设备墙钟会被拨动，不能直接拿来判窗口；[ServerClock] 的锚点由 `link.hello_ack` /
 * `link.ping` 建立，再用单调钟外推。没有锚点时条件未决，调用方按隐藏处理。
 */
object ConditionClock {

    private const val MINUTE_MS = 60_000L
    private const val ANCHOR_RETRY_MS = 30_000L

    /** 当前时刻的本地读数；还没有服务端锚点时给 null。 */
    fun now(): LocalMoment? {
        val epochMs = serverNowMs() ?: return null
        return localMoment(epochMs, TimeZone.getDefault())
    }

    /** 距下一个整分钟的毫秒数；没有锚点时给 30 秒让调用方稍后重试。 */
    fun delayUntilNextMinute(): Long = delayUntilNextMinute(serverNowMs())

    /**
     * 距下一个整分钟的毫秒数。
     *
     * 余数为 0 时返回一整分钟，避免在边界上原地重排。
     */
    fun delayUntilNextMinute(serverMs: Long?): Long {
        if (serverMs == null) return ANCHOR_RETRY_MS
        val remainder = ((serverMs % MINUTE_MS) + MINUTE_MS) % MINUTE_MS
        return MINUTE_MS - remainder
    }

    /**
     * 服务端时刻（Unix 毫秒）→ 设备本地日历读数。
     *
     * `Calendar.DAY_OF_WEEK` 的周日是 1，这里换成 ISO（周一=1…周日=7）。
     */
    fun localMoment(epochMs: Long, timeZone: TimeZone): LocalMoment {
        val calendar = Calendar.getInstance(timeZone).apply { timeInMillis = epochMs }
        val dayOfWeek = calendar.get(Calendar.DAY_OF_WEEK)
        val isoWeekday = if (dayOfWeek == Calendar.SUNDAY) 7 else dayOfWeek - 1
        val previousIsoWeekday = if (isoWeekday == 1) 7 else isoWeekday - 1
        val minuteOfDay = calendar.get(Calendar.HOUR_OF_DAY) * 60 + calendar.get(Calendar.MINUTE)
        return LocalMoment(isoWeekday, previousIsoWeekday, minuteOfDay)
    }

    private fun serverNowMs(): Long? = ServerClock.nowRfc3339()?.let { Clock.parseRfc3339(it)?.time }
}
