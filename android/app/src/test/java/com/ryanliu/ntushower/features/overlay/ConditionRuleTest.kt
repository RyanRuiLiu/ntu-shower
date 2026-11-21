package com.ryanliu.ntushower.features.overlay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class ConditionRuleTest {

    private fun settings(
        timeEnabled: Boolean = false,
        timeStart: String = "22:00",
        timeEnd: String = "23:00",
        weekdays: String = "1,2,3,4,5,6,7",
        occupancyEnabled: Boolean = false,
        occupancyMin: Int = 0,
        occupancyMax: Int = 48,
    ): ConditionSettings = ConditionSettings(
        timeEnabled = timeEnabled,
        timeStart = timeStart,
        timeEnd = timeEnd,
        weekdays = weekdays,
        occupancyEnabled = occupancyEnabled,
        occupancyMin = occupancyMin,
        occupancyMax = occupancyMax,
    )

    private fun decide(
        settings: ConditionSettings,
        moment: LocalMoment? = null,
        occupied: Int? = null,
        coverVisible: Boolean = false,
    ): ConditionDecision = ConditionRule.decide(settings, ConditionInputs(moment, occupied, coverVisible))

    private fun moment(isoWeekday: Int, previousIsoWeekday: Int, minuteOfDay: Int): LocalMoment =
        LocalMoment(isoWeekday, previousIsoWeekday, minuteOfDay)

    private fun utcMillis(year: Int, month: Int, day: Int, hour: Int, minute: Int): Long =
        Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
            clear()
            set(year, month, day, hour, minute, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis

    @Test
    fun coverHidesBeforeConditions() {
        val decision = decide(
            settings(occupancyEnabled = true, timeEnabled = true),
            moment = null,
            occupied = null,
            coverVisible = true,
        )

        assertEquals(OverlayReason.COVER_VISIBLE, decision.reason)
    }

    @Test
    fun occupancyUnknownHides() {
        val decision = decide(settings(occupancyEnabled = true))

        assertEquals(OverlayReason.OCCUPANCY_UNAVAILABLE, decision.reason)
    }

    @Test
    fun occupancyRangeIsInclusive() {
        val rule = settings(occupancyEnabled = true, occupancyMin = 3, occupancyMax = 5)

        assertTrue(decide(rule, occupied = 3).visible)
        assertTrue(decide(rule, occupied = 5).visible)
        assertEquals(OverlayReason.OUTSIDE_OCCUPANCY, decide(rule, occupied = 2).reason)
        assertEquals(OverlayReason.OUTSIDE_OCCUPANCY, decide(rule, occupied = 6).reason)
    }

    @Test
    fun invalidRangeHides() {
        val decision = decide(settings(occupancyEnabled = true, occupancyMin = 5, occupancyMax = 3), occupied = 4)

        assertEquals(OverlayReason.INVALID_RANGE, decision.reason)
    }

    @Test
    fun maxZeroAllowsOnlyZeroOccupied() {
        val rule = settings(occupancyEnabled = true, occupancyMin = 0, occupancyMax = 0)

        assertTrue(decide(rule, occupied = 0).visible)
        assertEquals(OverlayReason.OUTSIDE_OCCUPANCY, decide(rule, occupied = 1).reason)
    }

    @Test
    fun timeDisabledIgnoresMissingAnchor() {
        assertTrue(decide(settings(), moment = null).visible)
    }

    @Test
    fun timeEnabledWithoutAnchorHides() {
        assertEquals(OverlayReason.TIME_UNAVAILABLE, decide(settings(timeEnabled = true)).reason)
    }

    @Test
    fun unparsableTimeEndHidesAsInvalidTime() {
        // 配置的时刻坏掉（服务端 spec.parse_time 的对侧拒绝）即隐藏：屏幕提示的
        // 时段窗宁可不见，也不按猜出来的窗显示
        val decision = decide(settings(timeEnabled = true, timeEnd = "24:61"), moment = moment(1, 7, 22 * 60))

        assertEquals(OverlayReason.INVALID_TIME, decision.reason)
    }

    @Test
    fun sameDayWindowStartsInclusiveEndsExclusive() {
        val rule = settings(timeEnabled = true, timeStart = "22:00", timeEnd = "23:00", weekdays = "1")

        assertEquals(OverlayReason.OUTSIDE_TIME, decide(rule, moment(1, 7, 21 * 60 + 59)).reason)
        assertTrue(decide(rule, moment(1, 7, 22 * 60)).visible)
        assertTrue(decide(rule, moment(1, 7, 22 * 60 + 59)).visible)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(rule, moment(1, 7, 23 * 60)).reason)
    }

    @Test
    fun crossMidnightWindowBelongsToStartDay() {
        val fridayOnly = settings(
            timeEnabled = true,
            timeStart = "23:30",
            timeEnd = "00:30",
            weekdays = "5",
        )

        assertTrue(decide(fridayOnly, moment(5, 4, 23 * 60 + 40)).visible)
        assertTrue(decide(fridayOnly, moment(6, 5, 10)).visible)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(fridayOnly, moment(6, 5, 40)).reason)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(fridayOnly, moment(5, 4, 12 * 60)).reason)
    }

    @Test
    fun crossMidnightDoesNotBorrowTheNextDaySelection() {
        val saturdayOnly = settings(
            timeEnabled = true,
            timeStart = "23:30",
            timeEnd = "00:30",
            weekdays = "6",
        )

        assertEquals(OverlayReason.OUTSIDE_TIME, decide(saturdayOnly, moment(5, 4, 23 * 60 + 40)).reason)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(saturdayOnly, moment(6, 5, 10)).reason)
        assertTrue(decide(saturdayOnly, moment(6, 5, 23 * 60 + 40)).visible)
    }

    @Test
    fun startEqualsEndMeansAllDay() {
        val rule = settings(timeEnabled = true, timeStart = "08:00", timeEnd = "08:00", weekdays = "2")

        assertTrue(decide(rule, moment(2, 1, 0)).visible)
        assertTrue(decide(rule, moment(2, 1, 23 * 60 + 59)).visible)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(rule, moment(3, 2, 8 * 60)).reason)
    }

    @Test
    fun weekdaySelectionIsRequired() {
        val rule = settings(timeEnabled = true, timeStart = "00:00", timeEnd = "00:00", weekdays = "1,3,5")

        assertTrue(decide(rule, moment(3, 2, 12 * 60)).visible)
        assertEquals(OverlayReason.OUTSIDE_TIME, decide(rule, moment(2, 1, 12 * 60)).reason)
    }

    @Test
    fun invalidTimeHides() {
        assertEquals(
            OverlayReason.INVALID_TIME,
            decide(settings(timeEnabled = true, timeStart = "25:00"), moment(1, 7, 12 * 60)).reason,
        )
    }

    @Test
    fun invalidWeekdaysHides() {
        assertEquals(
            OverlayReason.INVALID_WEEKDAYS,
            decide(settings(timeEnabled = true, weekdays = "1,8"), moment(1, 7, 12 * 60)).reason,
        )
        assertEquals(
            OverlayReason.INVALID_WEEKDAYS,
            decide(settings(timeEnabled = true, weekdays = ""), moment(1, 7, 12 * 60)).reason,
        )
    }

    @Test
    fun occupancyIsCheckedBeforeTime() {
        val decision = decide(settings(occupancyEnabled = true, timeEnabled = true))

        assertEquals(OverlayReason.OCCUPANCY_UNAVAILABLE, decision.reason)
    }

    @Test
    fun localMomentConvertsUtcToDeviceTimeZone() {
        val moment = ConditionClock.localMoment(
            utcMillis(2026, Calendar.OCTOBER, 23, 14, 30),
            TimeZone.getTimeZone("Asia/Singapore"),
        )

        assertEquals(5, moment.isoWeekday)
        assertEquals(4, moment.previousIsoWeekday)
        assertEquals(22 * 60 + 30, moment.minuteOfDay)
    }

    @Test
    fun localMomentKeepsPreviousAsSundayAfterMidnight() {
        val moment = ConditionClock.localMoment(
            utcMillis(2026, Calendar.OCTOBER, 25, 17, 0),
            TimeZone.getTimeZone("Asia/Singapore"),
        )

        assertEquals(1, moment.isoWeekday)
        assertEquals(7, moment.previousIsoWeekday)
        assertEquals(60, moment.minuteOfDay)
    }

    @Test
    fun delayUntilNextMinuteAlignsToMinuteBoundary() {
        assertEquals(30_000L, ConditionClock.delayUntilNextMinute(null))
        assertEquals(60_000L, ConditionClock.delayUntilNextMinute(0L))
        assertEquals(59_999L, ConditionClock.delayUntilNextMinute(1L))
        assertEquals(1L, ConditionClock.delayUntilNextMinute(59_999L))
    }

    @Test
    fun parseWeekdaysNormalizesAndRejects() {
        assertEquals(setOf(1, 3), ConditionRule.parseWeekdays(" 3, 1, 3 "))

        assertEquals(null, ConditionRule.parseWeekdays(""))
        assertEquals(null, ConditionRule.parseWeekdays("1,,2"))
        assertEquals(null, ConditionRule.parseWeekdays("0"))
        assertEquals(null, ConditionRule.parseWeekdays("8"))
        assertEquals(null, ConditionRule.parseWeekdays("mon"))
    }
}
