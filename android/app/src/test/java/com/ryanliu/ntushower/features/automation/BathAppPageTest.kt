package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * 页面状态判定。
 *
 * 遍历本身依赖 `AccessibilityNodeInfo`（系统类），搬不进 JVM 单测；但「见到哪些特征
 * 该判成哪个状态」是纯逻辑——而那才是会出错的地方。优先级写反了，一个正在等输入的
 * 验证页会被判成首页，流程接着去点一个此刻不存在的按钮，错误还落在很远的地方。
 */
class BathAppPageTest {

    private fun features(block: BathAppPage.Features.() -> Unit): PageSnapshot =
        BathAppPage.Features().apply(block).toSnapshot()

    @Test
    fun `no feature means unknown`() {
        assertEquals(PageState.UNKNOWN, features { }.state)
    }

    @Test
    fun `each feature maps to its own state`() {
        assertEquals(PageState.HOME, features { hasHome = true }.state)
        assertEquals(PageState.ID_VERIFY, features { hasIdVerify = true }.state)
        assertEquals(PageState.HINT, features { hasHint = true }.state)
        assertEquals(PageState.VF_RESULT, features { hasVfResult = true }.state)
        assertEquals(PageState.BOOKING, features { hasBooking = true }.state)
        assertEquals(PageState.BOOKING_CODE, features { hasBookingCode = true }.state)
    }

    @Test
    fun `home wins over everything else`() {
        // HOME 最优先：它是稳定态，其余特征可能只是页面切换过程中的残留。
        // 反过来的话，一个残留的 tv_hint 就能把已经回到首页的流程判成"提示页"，
        // 于是这次操作被当成"浴室服务器拒绝"回报给调用方。
        val snapshot = features {
            hasHome = true
            hasIdVerify = true
            hasHint = true
            hasVfResult = true
            hasBooking = true
            hasBookingCode = true
        }
        assertEquals(PageState.HOME, snapshot.state)
    }

    @Test
    fun `id verify wins over the transient pages`() {
        // 停在 ID_VERIFY 上说明还在等输入，而 HINT / VF_RESULT 是"一闪过"的提示页
        val snapshot = features {
            hasIdVerify = true
            hasHint = true
            hasVfResult = true
        }
        assertEquals(PageState.ID_VERIFY, snapshot.state)
    }

    @Test
    fun `hint wins over vf result`() {
        val snapshot = features {
            hasHint = true
            hasVfResult = true
        }
        assertEquals(PageState.HINT, snapshot.state)
    }

    @Test
    fun `booking wins over booking code`() {
        val snapshot = features {
            hasBooking = true
            hasBookingCode = true
        }
        assertEquals(PageState.BOOKING, snapshot.state)
    }

    @Test
    fun `hint carries its text`() {
        val snapshot = features {
            hasHint = true
            hintText = "余额不足"
        }
        assertEquals("余额不足", snapshot.message)
    }

    @Test
    fun `vf result carries its text`() {
        val snapshot = features {
            hasVfResult = true
            vfResultText = "验证成功"
        }
        assertEquals("验证成功", snapshot.message)
    }

    @Test
    fun `only hint and vf result carry a message`() {
        // 其他页面没有"要转达给用户的话"。带上空串会让调用方以为有内容可显示
        assertEquals("", features { hasHome = true }.message)
        assertEquals("", features { hasIdVerify = true }.message)
        assertEquals("", features { hasBooking = true }.message)
        assertEquals("", features { }.message)
    }

    @Test
    fun `missing text falls back to empty rather than null`() {
        // 控件存在但没填字（text 为 null）不能让 null 漏到调用方——
        // 那会在拼 terminal_message 时变成字符串 "null" 显示给用户
        assertEquals("", features { hasHint = true }.message)
        assertEquals("", features { hasVfResult = true }.message)
    }
}
