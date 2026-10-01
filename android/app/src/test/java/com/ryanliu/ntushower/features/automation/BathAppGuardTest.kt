package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 崩溃守护决策逻辑：窗口计数、阈值冷却、冷却解除。 */
class BathAppGuardTest {

    private val params = BathAppGuard.Params(
        windowMs = 30_000L,
        maxCount = 3,
        cooldownMs = 600_000L,
    )

    private fun guard() = BathAppGuard()

    @Test
    fun `exits below threshold relaunch`() {
        val g = guard()
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(0, params))
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(1_000, params))
    }

    @Test
    fun `third exit within window starts cooldown`() {
        val g = guard()
        g.onBackground(0, params)
        g.onBackground(1_000, params)
        assertEquals(BathAppGuard.Action.COOLDOWN, g.onBackground(2_000, params))
        assertTrue(g.isCoolingDown(3_000))
    }

    @Test
    fun `cooldown expires after cooldown period`() {
        val g = guard()
        repeat(params.maxCount - 1) { g.onBackground(it * 1_000L, params) }
        g.onBackground(2_000, params)
        assertFalse(g.isCoolingDown(2_000 + params.cooldownMs))
    }

    @Test
    fun `foreground clears cooldown and history`() {
        val g = guard()
        repeat(params.maxCount) { g.onBackground(it * 1_000L, params) }
        assertTrue(g.isCoolingDown(3_000))
        g.onForeground()
        assertFalse(g.isCoolingDown(5_000))
        // 历史已清：再次单次退出不会立即冷却
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(6_000, params))
    }

    @Test
    fun `exits during cooldown stay in cooldown`() {
        val g = guard()
        repeat(params.maxCount) { g.onBackground(it * 1_000L, params) }

        // 冷却期内再次退出：不得重新计数，也不得拉起
        assertEquals(BathAppGuard.Action.COOLDOWN, g.onBackground(10_000, params))
        assertEquals(BathAppGuard.Action.COOLDOWN, g.onBackground(20_000, params))
    }

    @Test
    fun `cooldown is lifted only by returning to foreground`() {
        val g = guard()
        repeat(params.maxCount) { g.onBackground(it * 1_000L, params) }
        // 仅仅时间流逝不足以解除：期间仍然保持冷却
        assertEquals(BathAppGuard.Action.COOLDOWN, g.onBackground(300_000, params))
    }

    @Test
    fun `old exits outside window do not accumulate`() {
        val g = guard()
        g.onBackground(0, params)
        g.onBackground(1_000, params)
        // 第三次退出发生在窗口之外（>30s），窗口内只有 1 次
        assertEquals(BathAppGuard.Action.RELAUNCH, g.onBackground(60_000, params))
    }
}
