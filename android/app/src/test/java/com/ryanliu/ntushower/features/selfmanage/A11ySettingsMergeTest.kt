package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class A11ySettingsMergeTest {

    private val component = "com.ryanliu.ntushower/.service.A11yService"

    @Test
    fun `uses the component alone when the list is empty`() {
        assertEquals(component, A11ySettingsMerge.mergeSettingsValue("", component))
    }

    @Test
    fun `treats system null markers as empty`() {
        // settings get 对未设置项返回字符串 "null"
        assertEquals(component, A11ySettingsMerge.mergeSettingsValue("null", component))
        assertEquals(component, A11ySettingsMerge.mergeSettingsValue("none", component))
        assertEquals(component, A11ySettingsMerge.mergeSettingsValue("  ", component))
    }

    @Test
    fun `appends without dropping existing entries`() {
        // 设备上可能同时启用其他无障碍服务，覆盖等于把别人的功能关掉
        val merged = A11ySettingsMerge.mergeSettingsValue("com.other/.Service", component)

        assertEquals("com.other/.Service:$component", merged)
    }

    @Test
    fun `keeps the list unchanged when already present`() {
        val current = "com.other/.Service:$component"

        assertEquals(current, A11ySettingsMerge.mergeSettingsValue(current, component))
    }

    @Test
    fun `detects presence regardless of position`() {
        assertTrue(A11ySettingsMerge.contains("$component:com.other/.Service", component))
        assertTrue(A11ySettingsMerge.contains(component, component))
        assertFalse(A11ySettingsMerge.contains("com.other/.Service", component))
    }
}
