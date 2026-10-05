package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Test

class McuPowerScheduleTest {

    @Test
    fun `same day target counts forward`() {
        // 13:00 → 14:00：一小时
        assertEquals(60, McuPowerSchedule.delayMinutes(13 * 60, 14 * 60))
    }

    @Test
    fun `target already passed rolls to next day`() {
        // 23:49 → 次日 01:58：跨日加满一圈
        assertEquals(129, McuPowerSchedule.delayMinutes(23 * 60 + 49, 1 * 60 + 58))
    }

    @Test
    fun `late night target rolls past midnight`() {
        // 23:00 → 次日 00:30
        assertEquals(90, McuPowerSchedule.delayMinutes(23 * 60, 30))
    }

    @Test
    fun `same minute counts as zero`() {
        assertEquals(0, McuPowerSchedule.delayMinutes(10 * 60 + 30, 10 * 60 + 30))
    }

    @Test
    fun `on delay counts from the off moment`() {
        // 断电落在 01:58（118 分），上电目标 10:55（655 分）→ 相隔 8 小时 57 分
        assertEquals(8 * 60 + 57, McuPowerSchedule.delayMinutes(1 * 60 + 58, 10 * 60 + 55))
    }

    @Test
    fun `parse accepts valid clock times`() {
        assertEquals(23 * 60 + 58, McuPowerSchedule.parseMinuteOfDay("23:58"))
        assertEquals(0, McuPowerSchedule.parseMinuteOfDay("00:00"))
    }

    @Test
    fun `parse rejects malformed times`() {
        assertEquals(null, McuPowerSchedule.parseMinuteOfDay("24:00"))
        assertEquals(null, McuPowerSchedule.parseMinuteOfDay("12:60"))
        assertEquals(null, McuPowerSchedule.parseMinuteOfDay("9:41"))
        assertEquals(null, McuPowerSchedule.parseMinuteOfDay("abc"))
    }
}
