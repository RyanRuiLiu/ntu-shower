package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 无障碍链路的三个前提。
 *
 * 入参用**真实的组件名形态**（`包名/.service.A11yService`），与 [A11yGuard] 传给本判定的
 * 那份一致。用包名也能过——[A11ySettingsMerge.contains] 是子串包含——但那测的是一个真实
 * 调用不会出现的入参，也让人以为斜杠分隔是这套设置的格式。
 */
class A11yStateTest {

    private val component = "com.ryanliu.ntushower/.service.A11yService"
    private val fullList = "com.other/.Service:$component"

    @Test
    fun `accepts the enabled switch with trailing newline`() {
        assertTrue(A11yState.isSwitchOn("1"))
        assertTrue(A11yState.isSwitchOn("1\n"))
    }

    @Test
    fun `treats missing or malformed readings as off`() {
        // 判错方向的代价是整机无障碍失效，异常读数一律按未开启处理
        assertFalse(A11yState.isSwitchOn("0"))
        assertFalse(A11yState.isSwitchOn("null"))
        assertFalse(A11yState.isSwitchOn(""))
    }

    @Test
    fun `flags the switch being off even when the list is complete`() {
        // 真机上的故障形态之一：清单完好、总开关为 0，此前只查清单所以放行了
        assertTrue(A11yState.needsRepair("0", fullList, component, bound = true))
    }

    @Test
    fun `flags a missing component even when the switch is on`() {
        assertTrue(A11yState.needsRepair("1", "com.other/.Service", component, bound = true))
    }

    @Test
    fun `flags an unbound service even when both settings are correct`() {
        // 最隐蔽的一种：两个设置都对，系统侧却没绑上——只查设置会一路放行
        assertTrue(A11yState.needsRepair("1", fullList, component, bound = false))
    }

    @Test
    fun `reports no repair when all three conditions hold`() {
        assertFalse(A11yState.needsRepair("1\n", fullList, component, bound = true))
    }
}
