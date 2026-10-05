package com.ryanliu.ntushower.core.net

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class ReconnectBackoffTest {

    private val base = 5_000L
    private val max = 60_000L

    /** 等量抖动的取值区间：[cap/2, cap]。 */
    private fun assertInRange(delay: Long, lower: Long, upper: Long) {
        assertTrue("delay=$delay 应落在 [$lower, $upper]", delay in lower..upper)
    }

    @Test
    fun `delay stays between half and the full cap of each level`() {
        for (seed in 0 until 100) {
            val random = Random(seed)
            assertInRange(ReconnectBackoff.delayMs(1, base, max, random), lower = 2_500L, upper = 5_000L)
            assertInRange(ReconnectBackoff.delayMs(2, base, max, random), lower = 5_000L, upper = 10_000L)
            assertInRange(ReconnectBackoff.delayMs(4, base, max, random), lower = 20_000L, upper = 40_000L)
            assertInRange(ReconnectBackoff.delayMs(5, base, max, random), lower = 30_000L, upper = 60_000L)
        }
    }

    @Test
    fun `jitter actually spreads values within one level`() {
        // 同一退避级、不同随机种子的取值必须有散布——没有散布就退化回了无抖动的同步波次
        val values = (0 until 100).map { ReconnectBackoff.delayMs(5, base, max, Random(it)) }

        assertTrue("同一退避级应当有散布", values.toSet().size > 1)
        assertTrue(values.min() >= max / 2)
        assertTrue(values.max() <= max)
    }

    @Test
    fun `huge attempt count does not overflow the shift`() {
        // 移位位数不封顶会在 Int 上溢出成负数，退避就退化成"立即重连"
        val delay = ReconnectBackoff.delayMs(Int.MAX_VALUE, base, max, Random(42))

        assertInRange(delay, lower = 30_000L, upper = 60_000L)
    }

    @Test
    fun `attempt below one is treated as the first attempt`() {
        assertInRange(ReconnectBackoff.delayMs(0, base, max, Random(7)), lower = 2_500L, upper = 5_000L)
    }
}
