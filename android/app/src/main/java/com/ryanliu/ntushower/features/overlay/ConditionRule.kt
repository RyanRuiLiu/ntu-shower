package com.ryanliu.ntushower.features.overlay

import com.ryanliu.ntushower.core.clock.TimeOfDay

/** 共用条件与两个浮层共有的显隐原因；日志与测试都用这些常量。 */
object OverlayReason {
    const val VISIBLE = "visible"
    const val DISABLED = "disabled"
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
 * 屏幕浮层的唯一条件判定点：时间窗、占用数与遮盖层。
 *
 * 输入全部是纯数据：配置副本、服务端时刻换算出的 [LocalMoment]、本地占用喷头数、
 * 遮盖层是否可见。这里不读时钟、不读数据库、不碰 Android 视图。
 *
 * `start < end` 是同日窗口，`start > end` 是跨午夜窗口，`start == end` 表示全天。
 * 跨午夜窗口归属开始那一天：周五 23:30–00:30 覆盖周六凌晨，即使周六未选中。
 *
 * 占用数为 null 表示“当前读数不可用”；占用条件开启时按隐藏处理，不拿 0 或上一次
 * 的数字替代。遮盖层可见时隐藏，浮层因此不会浮在操作遮罩之上。
 */
object ConditionRule {

    fun decide(settings: ConditionSettings, inputs: ConditionInputs): ConditionDecision {
        if (inputs.coverVisible) return hidden(OverlayReason.COVER_VISIBLE)

        if (settings.occupancyEnabled) {
            if (inputs.occupied == null) return hidden(OverlayReason.OCCUPANCY_UNAVAILABLE)
            if (settings.occupancyMin > settings.occupancyMax) return hidden(OverlayReason.INVALID_RANGE)
            if (inputs.occupied < settings.occupancyMin || inputs.occupied > settings.occupancyMax) {
                return hidden(OverlayReason.OUTSIDE_OCCUPANCY)
            }
        }

        if (settings.timeEnabled) {
            val current = inputs.moment ?: return hidden(OverlayReason.TIME_UNAVAILABLE)
            val start = TimeOfDay.parseMinuteOfDay(settings.timeStart)
                ?: return hidden(OverlayReason.INVALID_TIME)
            val end = TimeOfDay.parseMinuteOfDay(settings.timeEnd)
                ?: return hidden(OverlayReason.INVALID_TIME)
            val days = parseWeekdays(settings.weekdays)
                ?: return hidden(OverlayReason.INVALID_WEEKDAYS)
            if (!isWithinWindow(days, start, end, current)) return hidden(OverlayReason.OUTSIDE_TIME)
        }

        return ConditionDecision(true, OverlayReason.VISIBLE)
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

    private fun hidden(reason: String): ConditionDecision = ConditionDecision(false, reason)
}
