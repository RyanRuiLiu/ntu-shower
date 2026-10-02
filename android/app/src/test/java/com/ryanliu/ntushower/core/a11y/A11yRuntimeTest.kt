package com.ryanliu.ntushower.core.a11y

import android.content.Intent
import android.view.accessibility.AccessibilityNodeInfo
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
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
    }

    @Test
    fun `mark advances on every refresh`() {
        val before = A11yRuntime.pageChangeMark()
        A11yRuntime.onPageRefreshed()

        assertEquals(before + 1, A11yRuntime.pageChangeMark())
    }

    @Test(timeout = 5_000)
    fun `awaitPageChangeAfter wakes as soon as the count moves`() {
        val mark = A11yRuntime.pageChangeMark()
        Thread {
            Thread.sleep(50)
            A11yRuntime.onPageRefreshed()
        }.start()

        val startedAt = System.nanoTime()
        A11yRuntime.awaitPageChangeAfter(mark, 10_000)

        assertTrue("应在远小于超时的时间内返回", System.nanoTime() - startedAt < 5_000_000_000L)
    }

    @Test
    fun `awaitPageChange returns on timeout when nothing happens`() {
        val startedAt = System.nanoTime()

        A11yRuntime.awaitPageChange(50)

        assertTrue("应在超时处返回而不是提前醒", System.nanoTime() - startedAt >= 40_000_000L)
    }

    // awaitPageChangeAfter 的超时分支不在 JVM 上测：它的循环按单调时钟算剩余时长，
    // 而单测里 SystemClock 桩恒返 0（见 build.gradle 的 unitTests 注释），时钟不推进
    // 就模拟不出"等到超时"。该分支随真机流程覆盖。

    @Test(timeout = 5_000)
    fun `awaitPageChange wakes on the next refresh`() {
        Thread {
            Thread.sleep(50)
            A11yRuntime.onPageRefreshed()
        }.start()

        val startedAt = System.nanoTime()
        A11yRuntime.awaitPageChange(10_000)

        assertTrue("应在远小于超时的时间内返回", System.nanoTime() - startedAt < 5_000_000_000L)
    }
}
