package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.TimeZone

class TimeSyncTest {

    /** 2026-10-02T05:11:14Z。 */
    private val epochMs = 1_790_917_874_000L

    @Test
    fun shellDatetime_formats_in_device_local_timezone() {
        val shaanxi = TimeZone.getTimeZone("Asia/Shanghai")

        assertEquals("20261002.131114", TimeSync.shellDatetime(epochMs, shaanxi))
    }

    @Test
    fun shellDatetime_same_wall_time_in_different_zone_shifts_the_epoch() {
        val utc = TimeZone.getTimeZone("UTC")

        assertEquals("20261002.131114", TimeSync.shellDatetime(epochMs + 8 * 3_600_000L, utc))
    }
}
