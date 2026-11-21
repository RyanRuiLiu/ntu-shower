package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerClockTest {

    @Test
    fun `anchors on server time and keeps the last valid one`() {
        // 锚点是单例状态，跨用例泄漏：每个用例开头先清空，再断言"没有锚点"的分支。
        //
        // 本环境未 mock Android 框架（unitTests.isReturnDefaultValues），所以
        // Uptime.nowMs() 恒为 0，"锚点 + 走过的毫秒数"退化成锚点本身；真机上那个
        // 增量由 SystemClock.elapsedRealtime 提供，不是这里能验的东西。
        ServerClock.reset()
        assertNull("未锚定时必须返回 null，调用方据此不携带 captured_at", ServerClock.nowRfc3339())

        ServerClock.anchor("2026-10-02T05:11:14.000Z")
        assertEquals("2026-10-02T05:11:14.000Z", ServerClock.nowRfc3339())

        // 坏值不动锚点：宁可用一个稍旧的锚点，也不能让坏值把时间轴带偏
        ServerClock.anchor("not-a-time")
        assertEquals("2026-10-02T05:11:14.000Z", ServerClock.nowRfc3339())
    }

    @Test
    fun `re anchoring replaces the anchor`() {
        ServerClock.reset()
        ServerClock.anchor("2026-10-02T05:11:14.000Z")
        ServerClock.anchor("2026-10-02T06:11:14.000Z")
        assertEquals("2026-10-02T06:11:14.000Z", ServerClock.nowRfc3339())
    }
}
