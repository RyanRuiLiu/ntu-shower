package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class A11yStateTest {

    private val appPackage = "com.ryanliu.ntushower"
    private val component = "$appPackage/.service/A11yService"
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
        assertTrue(A11yState.needsRepair("0", fullList, appPackage, bound = true))
    }

    @Test
    fun `flags a missing component even when the switch is on`() {
        assertTrue(A11yState.needsRepair("1", "com.other/.Service", appPackage, bound = true))
    }

    @Test
    fun `flags an unbound service even when both settings are correct`() {
        // 最隐蔽的一种：两个设置都对，系统侧却没绑上——只查设置会一路放行
        assertTrue(A11yState.needsRepair("1", fullList, appPackage, bound = false))
    }

    @Test
    fun `reports no repair when all three conditions hold`() {
        assertFalse(A11yState.needsRepair("1\n", fullList, appPackage, bound = true))
    }
}
