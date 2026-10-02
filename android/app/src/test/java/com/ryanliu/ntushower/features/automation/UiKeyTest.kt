package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 系统按键登记表。keyCode 与取值集合是跨端契约（服务端与网页端按这套取值），
 * 这里把代表性取值钉住；解析是大小写不敏感的全名匹配。
 */
class UiKeyTest {

    @Test
    fun `parse is case insensitive`() {
        assertEquals(UiKey.HOME, UiKey.parse("home"))
        assertEquals(UiKey.HOME, UiKey.parse("HOME"))
    }

    @Test
    fun `unknown name yields null for the caller to reject`() {
        assertNull(UiKey.parse("power"))
        assertNull(UiKey.parse(""))
    }

    @Test
    fun `key codes match the android contract`() {
        assertEquals(4, UiKey.BACK.keyCode)
        assertEquals(3, UiKey.HOME.keyCode)
        assertEquals(66, UiKey.ENTER.keyCode)
        assertEquals(67, UiKey.DEL.keyCode)
    }
}
