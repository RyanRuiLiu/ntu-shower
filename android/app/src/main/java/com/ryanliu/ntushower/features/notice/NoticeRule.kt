package com.ryanliu.ntushower.features.notice

import com.ryanliu.ntushower.core.clock.TimeOfDay
import java.util.Calendar
import java.util.TimeZone

/** 水平对齐的取值；与配置登记表 `notice_align` 的 choices 逐字一致。 */
object NoticeAlign {
    const val LEFT = "左对齐"
    const val CENTER = "居中"
    const val RIGHT = "靠右"
}

/** 一次判定的全部配置输入；由控制器在主线程从 RuntimeConfig 复制。 */
data class NoticeSettings(
    val enabled: Boolean,
    val text: String,
    val textSizeSp: Int,
    val textColor: String,
    val align: String,
    val centerOffsetDp: Int,
    val xDp: Int,
    val yDp: Int,
    val timeEnabled: Boolean,
    val timeStart: String,
    val timeEnd: String,
    val weekdays: String,
    val occupancyEnabled: Boolean,
    val occupancyMin: Int,
    val occupancyMax: Int,
)

/**
 * 服务端时刻换算后的本地日历读数。
 *
 * `previousIsoWeekday` 只服务跨午夜时段：00:30 属于前一天开始的窗口时，星期要按
 * 前一天算。
 */
data class LocalMoment(val isoWeekday: Int, val previousIsoWeekday: Int, val minuteOfDay: Int)

/** 判定结果：显示，或隐藏并给出一个可记录的原因。 */
data class NoticeDecision(val visible: Boolean, val reason: String)

/** 隐藏原因的唯一出处；日志与测试都用这些常量。 */
object NoticeReason {
    const val VISIBLE = "visible"
    const val DISABLED = "disabled"
    const val EMPTY_TEXT = "empty_text"
    const val COVER_VISIBLE = "cover_visible"
    const val OCCUPANCY_UNAVAILABLE = "occupancy_unavailable"
    const val INVALID_RANGE = "invalid_range"
    const val OUTSIDE_OCCUPANCY = "outside_occupancy"
    const val TIME_UNAVAILABLE = "time_unavailable"
    const val INVALID_TIME = "invalid_time"
    const val INVALID_WEEKDAYS = "invalid_weekdays"
    const val OUTSIDE_TIME = "outside_time"
}

/**
 * 屏幕提示的唯一判定点。
 *
 * 输入全部是纯数据：配置副本、服务端时刻换算出的 [LocalMoment]、本地占用喷头数、
 * 遮盖层是否可见。这里不读时钟、不读数据库、不碰 Android 视图。
 *
 * 时间条件按服务端时间轴判断；[LocalMoment] 由控制器用设备系统时区从服务端时刻
 * 换算而来。`start < end` 是同日窗口，`start > end` 是跨午夜窗口，`start == end`
 * 表示全天。跨午夜窗口归属开始那一天：周五 23:30–00:30 覆盖周六凌晨，即使周六
 * 未选中。
 *
 * 占用数为 null 表示“当前读数不可用”；占用条件开启时按隐藏处理，不拿 0 或上一次
 * 的数字替代。遮盖层可见时隐藏，提示窗因此不会浮在操作遮罩之上。
 */
object NoticeRule {

    private const val MINUTE_MS = 60_000L
    private const val ANCHOR_RETRY_MS = 30_000L

    fun decide(settings: NoticeSettings, moment: LocalMoment?, occupied: Int?, coverVisible: Boolean): NoticeDecision {
        if (!settings.enabled) return hidden(NoticeReason.DISABLED)
        if (settings.text.trim().isEmpty()) return hidden(NoticeReason.EMPTY_TEXT)
        if (coverVisible) return hidden(NoticeReason.COVER_VISIBLE)

        if (settings.occupancyEnabled) {
            if (occupied == null) return hidden(NoticeReason.OCCUPANCY_UNAVAILABLE)
            if (settings.occupancyMin > settings.occupancyMax) return hidden(NoticeReason.INVALID_RANGE)
            if (occupied < settings.occupancyMin || occupied > settings.occupancyMax) {
                return hidden(NoticeReason.OUTSIDE_OCCUPANCY)
            }
        }

        if (settings.timeEnabled) {
            val current = moment ?: return hidden(NoticeReason.TIME_UNAVAILABLE)
            val start = TimeOfDay.parseMinuteOfDay(settings.timeStart)
                ?: return hidden(NoticeReason.INVALID_TIME)
            val end = TimeOfDay.parseMinuteOfDay(settings.timeEnd)
                ?: return hidden(NoticeReason.INVALID_TIME)
            val days = parseWeekdays(settings.weekdays)
                ?: return hidden(NoticeReason.INVALID_WEEKDAYS)
            if (!isWithinWindow(days, start, end, current)) return hidden(NoticeReason.OUTSIDE_TIME)
        }

        return NoticeDecision(true, NoticeReason.VISIBLE)
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

    /**
     * 距下一个整分钟的毫秒数。
     *
     * 没有服务端时间锚点时给 30 秒，由控制器稍后重试；有了锚点就对齐到分钟边界。
     * 余数为 0 时返回一整分钟，避免在边界上原地重排。
     */
    fun delayUntilNextMinute(serverMs: Long?): Long {
        if (serverMs == null) return ANCHOR_RETRY_MS
        val remainder = ((serverMs % MINUTE_MS) + MINUTE_MS) % MINUTE_MS
        return MINUTE_MS - remainder
    }

    /** `1,2,3` → `{1,2,3}`；空项、越界、非数字给 null。 */
    internal fun parseWeekdays(value: String): Set<Int>? {
        val parts = value.split(',').map { it.trim() }
        if (parts.isEmpty() || parts.any { it.isEmpty() }) return null
        val days = mutableSetOf<Int>()
        for (part in parts) {
            val day = part.toIntOrNull() ?: return null
            if (day !in 1..7) return null
            days.add(day)
        }
        return days
    }

    private fun isWithinWindow(days: Set<Int>, start: Int, end: Int, moment: LocalMoment): Boolean {
        val now = moment.minuteOfDay
        return when {
            start < end -> moment.isoWeekday in days && now >= start && now < end

            start > end -> (moment.isoWeekday in days && now >= start) ||
                (moment.previousIsoWeekday in days && now < end)

            else -> moment.isoWeekday in days
        }
    }

    private fun hidden(reason: String): NoticeDecision = NoticeDecision(false, reason)
}
