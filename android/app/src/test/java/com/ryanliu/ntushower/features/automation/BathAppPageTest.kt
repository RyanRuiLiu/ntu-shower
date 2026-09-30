package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Test

/** 判定规则认的那几个资源 ID。见 [BathAppPageTest] 里为什么不写成引用。 */
private const val RID_HOME = "com.datalook.bath:id/gv_sprinkler"
private const val RID_ID_VERIFY = "com.datalook.bath:id/ll_verify_idserial"
private const val RID_HINT = "com.datalook.bath:id/tv_hint"
private const val RID_VF_RESULT = "com.datalook.bath:id/tv_vf_result"
private const val RID_BOOKING = "com.datalook.bath:id/ll_booking"
private const val RID_BOOKING_CODE = "com.datalook.bath:id/ll_verify_bookingcode"

/**
 * 页面判定规则。
 *
 * 系统 API 那一侧（按 ID 查控件）搬不进 JVM 单测——`AccessibilityNodeInfo` 造不出来。但
 * 「见到哪些控件该判成哪个状态」是纯逻辑，而那才是会出错的地方：优先级写反了，一个正在等
 * 输入的验证页会被判成首页，流程接着去点一个此刻不存在的按钮，错误还落在很远的地方。
 *
 * 资源 ID 在这里**写死字面量**，不引用实现里的那份常量：常量本身写错时，跟着它一起错的
 * 测试什么都发现不了。这几个字符串是与浴室 App 之间的契约，值得在测试里另存一份。
 */
class BathAppPageTest {

    /** 内存版探针：只认这张表，并记下被问了哪些 ID。 */
    private class FakeProbe(private val present: Map<String, String> = emptyMap()) : PageProbe {

        val asked = mutableListOf<String>()

        override fun find(resId: String): PageElement? {
            asked += resId
            return present[resId]?.let { PageElement(it) }
        }
    }

    private fun decide(vararg present: Pair<String, String>): PageSnapshot =
        PageRules.decide(FakeProbe(present.toMap()))

    @Test
    fun `nothing found means unknown`() {
        assertEquals(PageState.UNKNOWN, decide().state)
    }

    @Test
    fun `each resource id maps to its own state`() {
        assertEquals(PageState.HOME, decide(RID_HOME to "").state)
        assertEquals(PageState.ID_VERIFY, decide(RID_ID_VERIFY to "").state)
        assertEquals(PageState.HINT, decide(RID_HINT to "").state)
        assertEquals(PageState.VF_RESULT, decide(RID_VF_RESULT to "").state)
        assertEquals(PageState.BOOKING, decide(RID_BOOKING to "").state)
        assertEquals(PageState.BOOKING_CODE, decide(RID_BOOKING_CODE to "").state)
    }

    @Test
    fun `home wins over everything else`() {
        // HOME 最优先：它是稳定态，其余特征可能只是页面切换过程中的残留。反过来的话，
        // 一个残留的 tv_hint 就能把已经回到首页的流程判成"提示页"，于是这次操作被当成
        // "浴室服务器拒绝"回报给调用方。
        val snapshot = decide(
            RID_HOME to "",
            RID_ID_VERIFY to "",
            RID_HINT to "余额不足",
            RID_VF_RESULT to "验证成功",
            RID_BOOKING to "",
            RID_BOOKING_CODE to "",
        )
        assertEquals(PageState.HOME, snapshot.state)
    }

    @Test
    fun `id verify wins over the transient pages`() {
        // 停在 ID_VERIFY 上说明还在等输入，而 HINT / VF_RESULT 是"一闪过"的提示页
        val snapshot = decide(RID_ID_VERIFY to "", RID_HINT to "请先登录", RID_VF_RESULT to "验证成功")
        assertEquals(PageState.ID_VERIFY, snapshot.state)
    }

    @Test
    fun `hint wins over vf result`() {
        assertEquals(PageState.HINT, decide(RID_HINT to "", RID_VF_RESULT to "").state)
    }

    @Test
    fun `booking wins over booking code`() {
        assertEquals(PageState.BOOKING, decide(RID_BOOKING to "", RID_BOOKING_CODE to "").state)
    }

    @Test
    fun `hint carries its text`() {
        assertEquals("余额不足", decide(RID_HINT to "余额不足").message)
    }

    @Test
    fun `vf result carries its text`() {
        assertEquals("验证成功", decide(RID_VF_RESULT to "验证成功").message)
    }

    @Test
    fun `only hint and vf result carry a message`() {
        // 其他页面没有"要转达给用户的话"。带上空串会让调用方以为有内容可显示
        assertEquals("", decide(RID_HOME to "").message)
        assertEquals("", decide(RID_ID_VERIFY to "").message)
        assertEquals("", decide(RID_BOOKING to "").message)
        assertEquals("", decide().message)
    }

    @Test
    fun `missing text falls back to empty rather than null`() {
        // 控件存在但没填字不能让 null 漏到调用方——那会在拼 terminal_message 时
        // 变成字符串 "null" 显示给学生
        assertEquals("", decide(RID_HINT to "").message)
        assertEquals("", decide(RID_VF_RESULT to "").message)
    }

    @Test
    fun `home settles in a single query`() {
        // 这条盯的是性能，不是正确性。首页挂着跑马灯与时钟，界面每秒产生 16 次内容变化、
        // 每次变化都走一遍判定——多问一句就是每秒多 16 次跨进程调用。改成按 ID 查之前，
        // 逐层下钻单个页面 52 个节点、单次识别 41.6ms、常驻 0.68 个核。
        val probe = FakeProbe(mapOf(RID_HOME to ""))
        assertEquals(PageState.HOME, PageRules.decide(probe).state)
        assertEquals(listOf(RID_HOME), probe.asked)
    }

    @Test
    fun `unrecognised page asks about every candidate`() {
        // 认不出的页面是最坏情况：六个特征全问一遍才知道都不在。它只出现在页面切换途中，
        // 拿这个代价换"稳态只问一次"是划算的——但代价本身要写下来，别让它悄悄涨上去。
        val probe = FakeProbe()
        assertEquals(PageState.UNKNOWN, PageRules.decide(probe).state)
        assertEquals(6, probe.asked.size)
    }
}
