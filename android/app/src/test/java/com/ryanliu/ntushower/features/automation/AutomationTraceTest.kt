package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 耗时记录器。
 *
 * 它跑在自动化流程的关键路径上，所以**绝不能抛**：写文件失败（JVM 单测里没有
 * `/sdcard`，设备上也可能只读）、开关状态异常，都必须原地吞掉。记录是用来观察流程的，
 * 它自己把流程搞挂就本末倒置了。
 */
class AutomationTraceTest {

    @Test
    fun `begin never throws regardless of the switch`() {
        // 不断言开关的当前值：RuntimeConfig 是单例，别的测试 apply 配置时会把开关打开，
        // 顺序一变结论就变。这里只保证它不抛——开关本身的行为由 RuntimeConfigTest 覆盖。
        AutomationTrace.begin("assign door=1")
    }

    @Test
    fun `mark and end never throw even when the target path is unwritable`() {
        // JVM 上 AppConfig.WORK_DIR 指向设备路径，写入必然失败——正是要覆盖的路径
        val trace = AutomationTrace.Trace("unit-test")
        trace.mark("first")
        trace.mark("second")
        trace.end("ok")
    }

    @Test
    fun `recordRefresh is a no-op while disabled`() {
        // 界面每秒产生十几次重算，开关关着时不该有任何副作用
        AutomationTrace.recordRefresh(costMs = 12, state = "HOME", changed = false)
    }

    @Test
    fun `mark can be called after end without throwing`() {
        // 调用顺序不该被强约束到"用错就崩"——记录器的错误不值得中断一次真实操作
        val trace = AutomationTrace.Trace("unit-test")
        trace.mark("only")
        trace.end("timeout")
        trace.mark("late")
    }

    @Test
    fun `trace label is carried through`() {
        // 标签是区分 assign / settle 的唯一依据，丢了它一轮记录就没法归因
        val trace = AutomationTrace.Trace("assign door=6")
        val label = trace.javaClass.getDeclaredField("label")
        label.isAccessible = true
        assertTrue((label.get(trace) as String).startsWith("assign"))
    }
}

/**
 * 重算统计的攒批逻辑。
 *
 * 它决定落盘那行里几个数对不对，而按这些数做的是"要不要再优化"的判断——数错了比不记更糟，
 * 因为结论会跟现实对不上，还看不出来。
 */
class RefreshWindowTest {

    @Test
    fun `nothing comes out before the batch is full`() {
        val window = RefreshWindow(flushEvery = 3)
        assertNull(window.add(costMs = 1, state = "HOME", changed = false, nowMs = 0))
        assertNull(window.add(costMs = 2, state = "HOME", changed = false, nowMs = 10))
    }

    @Test
    fun `a full batch reports count span average peak and changes`() {
        val window = RefreshWindow(flushEvery = 3)
        window.add(costMs = 4, state = "HOME", changed = false, nowMs = 1_000)
        window.add(costMs = 6, state = "HINT", changed = true, nowMs = 1_100)
        val line = window.add(costMs = 20, state = "HOME", changed = false, nowMs = 1_300)
        assertEquals("refresh n=3 span=300ms avg=10ms max=20ms qavg=0ms qmax=0ms changed=1 last=HOME", line)
    }

    @Test
    fun `query time is averaged and peaked on its own`() {
        // 查询那一段要单独看：总耗时看不出 53ms 里"取根节点"和"按 ID 查"各占多少，
        // 而这两笔的可优化空间完全不同
        val window = RefreshWindow(flushEvery = 2)
        window.addQuery(3)
        window.add(costMs = 40, state = "HOME", changed = false, nowMs = 0)
        window.addQuery(9)
        val line = window.add(costMs = 50, state = "HOME", changed = false, nowMs = 10)

        assertEquals("refresh n=2 span=10ms avg=45ms max=50ms qavg=6ms qmax=9ms changed=0 last=HOME", line)
    }

    @Test
    fun `the window restarts after every batch`() {
        // 不重启的话第二行就是累计值：跨度越拉越长、均值越来越平，趋势全被抹掉
        val window = RefreshWindow(flushEvery = 2)
        window.add(costMs = 2, state = "HOME", changed = false, nowMs = 0)
        assertEquals(
            "refresh n=2 span=10ms avg=2ms max=2ms qavg=0ms qmax=0ms changed=0 last=HOME",
            window.add(costMs = 2, state = "HOME", changed = false, nowMs = 10),
        )
        window.add(costMs = 4, state = "HOME", changed = false, nowMs = 100)
        assertEquals(
            "refresh n=2 span=10ms avg=4ms max=4ms qavg=0ms qmax=0ms changed=0 last=HOME",
            window.add(costMs = 4, state = "HOME", changed = false, nowMs = 110),
        )
    }
}
