package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 崩溃守护决策逻辑：窗口计数、阈值冷却、冷却解除。 */
class BathAppGuardTest {

    private val windowMs = 30_000L
    private val maxCount = 3
    private val cooldownMs = 600_000L

    private fun guard() = BathAppGuard(windowMs, maxCount, cooldownMs)

    @Test
    fun `exits below threshold relaunch`() {
        val g = guard()
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(0))
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(1_000))
    }

    @Test
    fun `third exit within window starts cooldown`() {
        val g = guard()
        g.onBackground(0)
        g.onBackground(1_000)
        assertEquals(BathAppGuard.Action.COOLDOWN, g.onBackground(2_000))
        assertTrue(g.isCoolingDown(3_000))
    }

    @Test
    fun `cooldown expires after cooldown period`() {
        val g = guard()
        repeat(maxCount - 1) { g.onBackground(it * 1_000L) }
        g.onBackground(2_000)
        assertFalse(g.isCoolingDown(2_000 + cooldownMs))
    }

    @Test
    fun `foreground clears cooldown and history`() {
        val g = guard()
        repeat(maxCount) { g.onBackground(it * 1_000L) }
        assertTrue(g.isCoolingDown(3_000))
        g.onForeground()
        assertFalse(g.isCoolingDown(5_000))
        // 历史已清：再次单次退出不会立即冷却
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(6_000))
    }

    @Test
    fun `old exits outside window do not accumulate`() {
        val g = guard()
        g.onBackground(0)
        g.onBackground(1_000)
        // 第三次退出发生在窗口之外（>30s），窗口内只有 1 次
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(60_000))
    }
}
