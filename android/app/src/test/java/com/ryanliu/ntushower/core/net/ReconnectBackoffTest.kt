package com.ryanliu.ntushower.core.net

import org.junit.Assert.assertEquals
import org.junit.Test

class ReconnectBackoffTest {

    private val base = 5_000L
    private val max = 60_000L

    @Test
    fun `first attempt waits the base delay`() {
        assertEquals(base, ReconnectBackoff.delayMs(1, base, max))
    }

    @Test
    fun `delay doubles per attempt`() {
        assertEquals(10_000L, ReconnectBackoff.delayMs(2, base, max))
        assertEquals(20_000L, ReconnectBackoff.delayMs(3, base, max))
        assertEquals(40_000L, ReconnectBackoff.delayMs(4, base, max))
    }

    @Test
    fun `delay is capped at the maximum`() {
        // 5 次以上继续翻倍会超过上限：必须封顶，否则设备长时间失联
        assertEquals(max, ReconnectBackoff.delayMs(5, base, max))
        assertEquals(max, ReconnectBackoff.delayMs(50, base, max))
    }

    @Test
    fun `huge attempt count does not overflow the shift`() {
        // 移位位数不封顶会在 Int 上溢出成负数，退避就退化成"立即重连"
        val delay = ReconnectBackoff.delayMs(Int.MAX_VALUE, base, max)

        assertEquals(max, delay)
    }

    @Test
    fun `attempt below one is treated as the first attempt`() {
        assertEquals(base, ReconnectBackoff.delayMs(0, base, max))
    }
}
