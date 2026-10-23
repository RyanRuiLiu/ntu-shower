package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
import org.junit.Test

class TimeOfDayTest {

    @Test
    fun `parse accepts valid clock times`() {
        assertEquals(23 * 60 + 58, TimeOfDay.parseMinuteOfDay("23:58"))
        assertEquals(0, TimeOfDay.parseMinuteOfDay("00:00"))
        assertEquals(22 * 60, TimeOfDay.parseMinuteOfDay("22:00"))
    }

    @Test
    fun `parse rejects malformed times`() {
        assertEquals(null, TimeOfDay.parseMinuteOfDay("24:00"))
        assertEquals(null, TimeOfDay.parseMinuteOfDay("12:60"))
        assertEquals(null, TimeOfDay.parseMinuteOfDay("9:41"))
        assertEquals(null, TimeOfDay.parseMinuteOfDay("abc"))
        assertEquals(null, TimeOfDay.parseMinuteOfDay(""))
    }
}
