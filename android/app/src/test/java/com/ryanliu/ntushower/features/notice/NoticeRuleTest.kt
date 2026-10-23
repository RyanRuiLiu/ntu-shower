package com.ryanliu.ntushower.features.notice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.TimeZone

class NoticeRuleTest {

    private fun settings(
        enabled: Boolean = true,
        text: String = "提示",
        timeEnabled: Boolean = false,
        timeStart: String = "22:00",
        timeEnd: String = "23:00",
        weekdays: String = "1,2,3,4,5,6,7",
        occupancyEnabled: Boolean = false,
        occupancyMin: Int = 0,
        occupancyMax: Int = 48,
    ): NoticeSettings = NoticeSettings(
        enabled = enabled,
        text = text,
        textSizeSp = 48,
        textColor = "#d94f16",
        xDp = 40,
        yDp = 40,
        timeEnabled = timeEnabled,
        timeStart = timeStart,
        timeEnd = timeEnd,
        weekdays = weekdays,
        occupancyEnabled = occupancyEnabled,
        occupancyMin = occupancyMin,
        occupancyMax = occupancyMax,
    )

    private fun moment(isoWeekday: Int, previousIsoWeekday: Int, minuteOfDay: Int): LocalMoment =
        LocalMoment(isoWeekday, previousIsoWeekday, minuteOfDay)

    private fun utcMillis(year: Int, month: Int, day: Int, hour: Int, minute: Int): Long =
        Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
            clear()
            set(year, month, day, hour, minute, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis

    @Test
    fun disabledHidesFirst() {
        val decision = NoticeRule.decide(settings(enabled = false, text = ""), null, null, coverVisible = true)

        assertEquals(NoticeReason.DISABLED, decision.reason)
        assertFalse(decision.visible)
    }

    @Test
    fun blankTextHidesBeforeCoverAndConditions() {
        val decision = NoticeRule.decide(settings(text = "   "), null, null, coverVisible = true)

        assertEquals(NoticeReason.EMPTY_TEXT, decision.reason)
    }

    @Test
    fun coverHidesBeforeConditions() {
        val decision = NoticeRule.decide(
            settings(occupancyEnabled = true, timeEnabled = true),
            moment = null,
            occupied = null,
            coverVisible = true,
        )

        assertEquals(NoticeReason.COVER_VISIBLE, decision.reason)
    }

    @Test
    fun occupancyUnknownHides() {
        val decision = NoticeRule.decide(settings(occupancyEnabled = true), null, null, coverVisible = false)

        assertEquals(NoticeReason.OCCUPANCY_UNAVAILABLE, decision.reason)
    }

    @Test
    fun occupancyRangeIsInclusive() {
        val rule = settings(occupancyEnabled = true, occupancyMin = 3, occupancyMax = 5)

        assertTrue(NoticeRule.decide(rule, null, 3, false).visible)
        assertTrue(NoticeRule.decide(rule, null, 5, false).visible)
        assertEquals(NoticeReason.OUTSIDE_OCCUPANCY, NoticeRule.decide(rule, null, 2, false).reason)
        assertEquals(NoticeReason.OUTSIDE_OCCUPANCY, NoticeRule.decide(rule, null, 6, false).reason)
    }

    @Test
    fun invalidRangeHides() {
        val decision = NoticeRule.decide(
            settings(occupancyEnabled = true, occupancyMin = 5, occupancyMax = 3),
            null,
            4,
            coverVisible = false,
        )

        assertEquals(NoticeReason.INVALID_RANGE, decision.reason)
    }

    @Test
    fun maxZeroAllowsOnlyZeroOccupied() {
        val rule = settings(occupancyEnabled = true, occupancyMin = 0, occupancyMax = 0)

        assertTrue(NoticeRule.decide(rule, null, 0, false).visible)
        assertEquals(NoticeReason.OUTSIDE_OCCUPANCY, NoticeRule.decide(rule, null, 1, false).reason)
    }

    @Test
    fun timeDisabledIgnoresMissingAnchor() {
        val decision = NoticeRule.decide(settings(), moment = null, occupied = null, coverVisible = false)

        assertTrue(decision.visible)
    }

    @Test
    fun timeEnabledWithoutAnchorHides() {
        val decision = NoticeRule.decide(
            settings(timeEnabled = true),
            moment = null,
            occupied = null,
            coverVisible = false,
        )

        assertEquals(NoticeReason.TIME_UNAVAILABLE, decision.reason)
    }

    @Test
    fun sameDayWindowStartsInclusiveEndsExclusive() {
        val rule = settings(timeEnabled = true, timeStart = "22:00", timeEnd = "23:00", weekdays = "1")

        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(rule, moment(1, 7, 21 * 60 + 59), null, false).reason)
        assertTrue(NoticeRule.decide(rule, moment(1, 7, 22 * 60), null, false).visible)
        assertTrue(NoticeRule.decide(rule, moment(1, 7, 22 * 60 + 59), null, false).visible)
        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(rule, moment(1, 7, 23 * 60), null, false).reason)
    }

    @Test
    fun crossMidnightWindowBelongsToStartDay() {
        val fridayOnly = settings(
            timeEnabled = true,
            timeStart = "23:30",
            timeEnd = "00:30",
            weekdays = "5",
        )

        assertTrue(NoticeRule.decide(fridayOnly, moment(5, 4, 23 * 60 + 40), null, false).visible)
        assertTrue(NoticeRule.decide(fridayOnly, moment(6, 5, 10), null, false).visible)
        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(fridayOnly, moment(6, 5, 40), null, false).reason)
        val fridayNoon = NoticeRule.decide(fridayOnly, moment(5, 4, 12 * 60), null, false)
        assertEquals(NoticeReason.OUTSIDE_TIME, fridayNoon.reason)
    }

    @Test
    fun crossMidnightDoesNotBorrowTheNextDaySelection() {
        val saturdayOnly = settings(
            timeEnabled = true,
            timeStart = "23:30",
            timeEnd = "00:30",
            weekdays = "6",
        )

        val fridayLate = NoticeRule.decide(saturdayOnly, moment(5, 4, 23 * 60 + 40), null, false)
        assertEquals(NoticeReason.OUTSIDE_TIME, fridayLate.reason)
        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(saturdayOnly, moment(6, 5, 10), null, false).reason)
        assertTrue(NoticeRule.decide(saturdayOnly, moment(6, 5, 23 * 60 + 40), null, false).visible)
    }

    @Test
    fun startEqualsEndMeansAllDay() {
        val rule = settings(timeEnabled = true, timeStart = "08:00", timeEnd = "08:00", weekdays = "2")

        assertTrue(NoticeRule.decide(rule, moment(2, 1, 0), null, false).visible)
        assertTrue(NoticeRule.decide(rule, moment(2, 1, 23 * 60 + 59), null, false).visible)
        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(rule, moment(3, 2, 8 * 60), null, false).reason)
    }

    @Test
    fun weekdaySelectionIsRequired() {
        val rule = settings(timeEnabled = true, timeStart = "00:00", timeEnd = "00:00", weekdays = "1,3,5")

        assertTrue(NoticeRule.decide(rule, moment(3, 2, 12 * 60), null, false).visible)
        assertEquals(NoticeReason.OUTSIDE_TIME, NoticeRule.decide(rule, moment(2, 1, 12 * 60), null, false).reason)
    }

    @Test
    fun invalidTimeHides() {
        val decision = NoticeRule.decide(
            settings(timeEnabled = true, timeStart = "25:00"),
            moment(1, 7, 12 * 60),
            null,
            coverVisible = false,
        )

        assertEquals(NoticeReason.INVALID_TIME, decision.reason)
    }

    @Test
    fun invalidWeekdaysHides() {
        assertEquals(
            NoticeReason.INVALID_WEEKDAYS,
            NoticeRule.decide(
                settings(timeEnabled = true, weekdays = "1,8"),
                moment(1, 7, 12 * 60),
                null,
                coverVisible = false,
            ).reason,
        )
        assertEquals(
            NoticeReason.INVALID_WEEKDAYS,
            NoticeRule.decide(
                settings(timeEnabled = true, weekdays = ""),
                moment(1, 7, 12 * 60),
                null,
                coverVisible = false,
            ).reason,
        )
    }

    @Test
    fun occupancyIsCheckedBeforeTime() {
        val decision = NoticeRule.decide(
            settings(occupancyEnabled = true, timeEnabled = true),
            moment = null,
            occupied = null,
            coverVisible = false,
        )

        assertEquals(NoticeReason.OCCUPANCY_UNAVAILABLE, decision.reason)
    }

    @Test
    fun localMomentConvertsUtcToDeviceTimeZone() {
        val moment = NoticeRule.localMoment(
            utcMillis(2026, Calendar.OCTOBER, 23, 14, 30),
            TimeZone.getTimeZone("Asia/Singapore"),
        )

        assertEquals(5, moment.isoWeekday)
        assertEquals(4, moment.previousIsoWeekday)
        assertEquals(22 * 60 + 30, moment.minuteOfDay)
    }

    @Test
    fun localMomentKeepsPreviousAsSundayAfterMidnight() {
        val moment = NoticeRule.localMoment(
            utcMillis(2026, Calendar.OCTOBER, 25, 17, 0),
            TimeZone.getTimeZone("Asia/Singapore"),
        )

        assertEquals(1, moment.isoWeekday)
        assertEquals(7, moment.previousIsoWeekday)
        assertEquals(60, moment.minuteOfDay)
    }

    @Test
    fun delayUntilNextMinuteAlignsToMinuteBoundary() {
        assertEquals(30_000L, NoticeRule.delayUntilNextMinute(null))
        assertEquals(60_000L, NoticeRule.delayUntilNextMinute(0L))
        assertEquals(59_999L, NoticeRule.delayUntilNextMinute(1L))
        assertEquals(1L, NoticeRule.delayUntilNextMinute(59_999L))
    }

    @Test
    fun parseWeekdaysNormalizesAndRejects() {
        assertEquals(setOf(1, 3), NoticeRule.parseWeekdays(" 3, 1, 3 "))

        assertEquals(null, NoticeRule.parseWeekdays(""))
        assertEquals(null, NoticeRule.parseWeekdays("1,,2"))
        assertEquals(null, NoticeRule.parseWeekdays("0"))
        assertEquals(null, NoticeRule.parseWeekdays("8"))
        assertEquals(null, NoticeRule.parseWeekdays("mon"))
    }
}
