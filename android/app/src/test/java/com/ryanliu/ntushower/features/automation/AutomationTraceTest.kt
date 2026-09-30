package com.ryanliu.ntushower.features.automation

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
        // 每秒一两行会持续堆积，开关关着时不该有任何副作用
        AutomationTrace.recordRefresh(costMs = 12, state = "HOME")
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
