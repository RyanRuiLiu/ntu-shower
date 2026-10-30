package com.ryanliu.ntushower.core.a11y

import android.content.Intent
import android.view.accessibility.AccessibilityNodeInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * 无障碍链路锚点：绑定、标志位与页面变化信令。
 *
 * 页面重算的事件源本来是 BathAppPage 的重算线程，这里用后台线程延迟放行来模拟。
 */
class A11yRuntimeTest {

    private class FakeSource : A11yNodeSource {
        override fun activeWindowRoot(): AccessibilityNodeInfo? = null
        override fun performClick(node: AccessibilityNodeInfo): Boolean = true
        override fun performSetText(node: AccessibilityNodeInfo, text: String): Boolean = true
        override fun clearClipboard() = Unit
        override fun launchBathApp(): Boolean = true
        override fun sendBroadcast(intent: Intent) = Unit
        override fun screenRotation(): Int = 0
    }

    @Test
    fun `bind registers the source and unbind clears it`() {
        val source = FakeSource()
        A11yRuntime.bind(source)
        assertSame(source, A11yRuntime.source)
        A11yRuntime.unbind()
        assertNull(A11yRuntime.source)
    }

    @Test
    fun `flags are writable and readable`() {
        A11yRuntime.bathAppInForeground = true
        assertTrue(A11yRuntime.bathAppInForeground)
        A11yRuntime.bathAppInForeground = false
        assertFalse(A11yRuntime.bathAppInForeground)

        A11yRuntime.automationInProgress = true
        assertTrue(A11yRuntime.automationInProgress)
        A11yRuntime.automationInProgress = false
        assertFalse(A11yRuntime.automationInProgress)

        A11yRuntime.bathFullLaunchPending = true
        assertTrue(A11yRuntime.bathFullLaunchPending)
        A11yRuntime.bathFullLaunchPending = false
        assertFalse(A11yRuntime.bathFullLaunchPending)
    }

    @Test
    fun `mark advances on every refresh`() {
        val before = A11yRuntime.pageChangeMark()
        A11yRuntime.onPageRefreshed()

        assertEquals(before + 1, A11yRuntime.pageChangeMark())
    }

    /**
     * 等到 [thread] 真正阻塞在 `wait` 上再返回。
     *
     * 断言"等待方已经睡着"才放行：通知若发生在开始等待之前，测试就没走到等待分支。
     * 用线程状态而不是 `sleep` 做同步，慢机器上也成立。
     */
    private fun awaitBlocked(thread: Thread) {
        val deadline = System.nanoTime() + 5_000_000_000L
        while (thread.state != Thread.State.WAITING && thread.state != Thread.State.TIMED_WAITING) {
            if (System.nanoTime() > deadline) fail("等待线程未在限时内进入阻塞")
            Thread.yield()
        }
    }

    @Test(timeout = 5_000)
    fun `awaitPageChangeAfter wakes when the count moves`() {
        val mark = A11yRuntime.pageChangeMark()
        val waiter = Thread { A11yRuntime.awaitPageChangeAfter(mark, 10_000) }
        waiter.start()
        awaitBlocked(waiter)
        A11yRuntime.onPageRefreshed()
        waiter.join(5_000)
        assertFalse("计数变化后等待方应结束", waiter.isAlive)
    }

    @Test(timeout = 5_000)
    fun `awaitPageChange returns on timeout when nothing happens`() {
        val waiter = Thread { A11yRuntime.awaitPageChange(50) }
        waiter.start()
        waiter.join(5_000)
        assertFalse("等待方应在超时后结束", waiter.isAlive)
    }

    // awaitPageChangeAfter 的超时分支不在 JVM 上测：它的循环按单调时钟算剩余时长，
    // 而单测里 SystemClock 桩恒返 0（见 build.gradle 的 unitTests 注释），时钟不推进
    // 就模拟不出"等到超时"。该分支随真机流程覆盖。

    @Test(timeout = 5_000)
    fun `awaitPageChange wakes on the next refresh`() {
        val waiter = Thread { A11yRuntime.awaitPageChange(10_000) }
        waiter.start()
        awaitBlocked(waiter)
        A11yRuntime.onPageRefreshed()
        waiter.join(5_000)
        assertFalse("刷新后等待方应结束", waiter.isAlive)
    }
}
