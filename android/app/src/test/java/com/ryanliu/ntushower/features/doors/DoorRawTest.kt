package com.ryanliu.ntushower.features.doors

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DoorRawTest {

    @Test
    fun parseRows_validLine() {
        val rows = DoorRaw.parseRows("1|0|99990052|1010209501|20260930003029|||0|0")
        assertEquals(1, rows.size)
        val d = rows[0]
        assertEquals(1, d.doorNo)
        assertEquals(0L, d.cardno)
        assertEquals("99990052", d.certno)
        assertEquals("1010209501", d.accno)
        assertEquals("20260930003029", d.openTime)
    }

    @Test
    fun parseRows_malformedReturnsEmpty() {
        assertEquals(0, DoorRaw.parseRows("bad line").size)
        assertEquals(0, DoorRaw.parseRows("").size)
    }

    @Test
    fun toPayload_convertsLocalTimeToUtc() {
        val d = DoorRaw.parseRows("1|0|99990052|1010209501|20260930003029|||0|0")[0]
        val payload = d.toPayload()
        assertEquals(1, payload.getInt("door_no"))
        assertEquals("99990052", payload.getString("cert_no"))
        assertNull(payload.opt("in_time")) // 空时间不上报
        assertNull(payload.opt("close_time"))
        assertEquals(0, payload.getInt("bath_time_s"))
        assertEquals(0, payload.getInt("status_raw"))
    }

    @Test
    fun toPayload_nonEmptyTimeBecomesRfc3339() {
        val d = DoorRaw.parseRows("1|0|99990052|1010209501|20260930003029|||0|0")[0]
        val payload = d.toPayload()
        assertTrue(payload.getString("open_time").endsWith("Z"))
    }
}
