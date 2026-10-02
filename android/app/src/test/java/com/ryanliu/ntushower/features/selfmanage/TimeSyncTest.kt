package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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

    @Test
    fun correction_threshold_is_inclusive_and_symmetric() {
        assertTrue(TimeSync.needsCorrection(5_000L, 5_000L))
        assertTrue(TimeSync.needsCorrection(-5_000L, 5_000L))
        assertFalse(TimeSync.needsCorrection(4_999L, 5_000L))
        assertFalse(TimeSync.needsCorrection(0L, 5_000L))

        // 开机校时的阈值来自远程配置（默认 60 秒）
        assertFalse(TimeSync.needsCorrection(59_999L, 60_000L))
        assertTrue(TimeSync.needsCorrection(60_000L, 60_000L))
    }
}
