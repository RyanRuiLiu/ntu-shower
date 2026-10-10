package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 崩溃守护决策逻辑：去向筛选、窗口计数、阈值冷却、冷却解除。 */
class BathAppGuardTest {

    private val params = BathAppGuard.Params(
        windowMs = 30_000L,
        maxCount = 3,
        cooldownMs = 600_000L,
    )

    /** 一个桌面包：只有退到这里才计数、才拉起。 */
    private val desktop = setOf("com.example.desktop")

    private fun guard() = BathAppGuard()

    /** 退到桌面。 */
    private fun desktopExit(g: BathAppGuard, at: Long) = g.onBackground(at, "com.example.desktop", desktop, params)

    /** 退到桌面以外的包（现场人员走调试通道）。 */
    private fun elsewhereExit(g: BathAppGuard, at: Long) = g.onBackground(at, "com.example.other", desktop, params)

    @Test
    fun `exits below threshold relaunch`() {
        val g = guard()
        assertEquals(BathAppGuard.Action.RELAUNCH, desktopExit(g, 0))
        assertEquals(BathAppGuard.Action.RELAUNCH, desktopExit(g, 1_000))
    }

    @Test
    fun `third exit within window starts cooldown`() {
        val g = guard()
        desktopExit(g, 0)
        desktopExit(g, 1_000)
        assertEquals(BathAppGuard.Action.COOLDOWN, desktopExit(g, 2_000))
        assertTrue(g.isCoolingDown(3_000))
    }

    @Test
    fun `cooldown expires after cooldown period`() {
        val g = guard()
        repeat(params.maxCount - 1) { desktopExit(g, it * 1_000L) }
        desktopExit(g, 2_000)
        assertFalse(g.isCoolingDown(2_000 + params.cooldownMs))
    }

    @Test
    fun `foreground clears cooldown and history`() {
        val g = guard()
        repeat(params.maxCount) { desktopExit(g, it * 1_000L) }
        assertTrue(g.isCoolingDown(3_000))
        g.onForeground()
        assertFalse(g.isCoolingDown(5_000))
        // 历史已清：再次单次退出不会立即冷却
        assertEquals(BathAppGuard.Action.RELAUNCH, desktopExit(g, 6_000))
    }

    @Test
    fun `exits during cooldown stay in cooldown`() {
        val g = guard()
        repeat(params.maxCount) { desktopExit(g, it * 1_000L) }

        // 冷却期内再次退出：不得重新计数，也不得拉起
        assertEquals(BathAppGuard.Action.COOLDOWN, desktopExit(g, 10_000))
        assertEquals(BathAppGuard.Action.COOLDOWN, desktopExit(g, 20_000))
    }

    @Test
    fun `cooldown is lifted only by returning to foreground`() {
        val g = guard()
        repeat(params.maxCount) { desktopExit(g, it * 1_000L) }
        // 仅仅时间流逝不足以解除：期间仍然保持冷却
        assertEquals(BathAppGuard.Action.COOLDOWN, desktopExit(g, 300_000))
    }

    @Test
    fun `old exits outside window do not accumulate`() {
        val g = guard()
        desktopExit(g, 0)
        desktopExit(g, 1_000)
        // 第三次退出发生在窗口之外（>30s），窗口内只有 1 次
        assertEquals(BathAppGuard.Action.RELAUNCH, desktopExit(g, 60_000))
    }

    /**
     * 退到桌面以外的包：既不拉起、也不记账。
     *
     * 那是现场人员走调试通道（设备断网时远程失效、又插不了线，这条通道是刻意留的）。
     * **记账会让走通道的人把阈值打满**：连着走几次之后，真需要拉起时反而落进冷却。
     */
    @Test
    fun `exit to a non-desktop package is neither relaunched nor counted`() {
        val g = guard()
        repeat(params.maxCount + 2) { elsewhereExit(g, it * 1_000L) }

        assertEquals(BathAppGuard.Action.NONE, elsewhereExit(g, 9_000))
        assertFalse("走调试通道不该触发冷却", g.isCoolingDown(10_000))
        // 历史没有累积：此后一次桌面退出仍是"窗口内第一次"
        assertEquals(BathAppGuard.Action.RELAUNCH, desktopExit(g, 20_000))
    }
}
