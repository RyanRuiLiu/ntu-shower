package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerClockTest {

    @Test
    fun `anchors on server time and keeps the last valid one`() {
        // 三件事在一个用例里验：`ServerClock` 是单例，而锚点是它的状态——拆成多个用例
        // 会因执行顺序不同而互相干扰，反而测不准"没有锚点"这个分支。
        //
        // 本环境未 mock Android 框架（unitTests.isReturnDefaultValues），所以
        // Uptime.nowMs() 恒为 0，"锚点 + 走过的毫秒数"退化成锚点本身；真机上那个
        // 增量由 SystemClock.elapsedRealtime 提供，不是这里能验的东西。
        assertNull("未锚定时必须返回 null，调用方据此不携带 captured_at", ServerClock.nowRfc3339())

        ServerClock.anchor("2026-10-02T05:11:14.000Z")
        assertEquals("2026-10-02T05:11:14.000Z", ServerClock.nowRfc3339())

        // 坏值不动锚点：宁可用一个稍旧的锚点，也不能让坏值把时间轴带偏
        ServerClock.anchor("not-a-time")
        assertEquals("2026-10-02T05:11:14.000Z", ServerClock.nowRfc3339())
    }
}
