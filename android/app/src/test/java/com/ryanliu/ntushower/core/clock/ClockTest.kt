package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.TimeZone
import java.util.concurrent.CountDownLatch

class ClockTest {

    @Test
    fun `format is utc rfc3339 with millisecond precision`() {
        val timestamp = Clock.nowRfc3339()
        assertTrue("missing Z suffix: $timestamp", timestamp.endsWith("Z"))

        // 可被反向解析且时区语义为 UTC
        val parser = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
        parser.parse(timestamp) // 抛异常即失败
        assertEquals(24, timestamp.length)
    }

    @Test
    fun `concurrent calls do not corrupt output`() {
        val threads = 8
        val iterations = 200
        val errors = java.util.concurrent.atomic.AtomicInteger(0)
        val done = CountDownLatch(threads)
        repeat(threads) {
            Thread {
                repeat(iterations) {
                    val ts = Clock.nowRfc3339()
                    if (ts.length != 24 || !ts.endsWith("Z")) {
                        errors.incrementAndGet()
                    }
                }
                done.countDown()
            }.start()
        }
        done.await()
        assertEquals(0, errors.get())
    }

    @Test
    fun `parse round-trips what format produces`() {
        val timestamp = Clock.nowRfc3339()
        val parsed = Clock.parseRfc3339(timestamp)
        assertTrue("应能反向解析：$timestamp", parsed != null)
        // 毫秒精度，允许格式化与解析之间的极小误差
        assertTrue(Math.abs((parsed ?: 0L) - System.currentTimeMillis()) < 5_000)
    }

    @Test
    fun `parse returns null on unusable input`() {
        // 空串、乱写、非 UTC 写法都要吸收成 null，而不是抛到调用栈上——
        // 这些值来自对端（deadline_ts），格式不由我们决定
        assertNull(Clock.parseRfc3339(""))
        assertNull(Clock.parseRfc3339("not-a-time"))
        assertNull(Clock.parseRfc3339("2026-10-01T03:00:00+08:00"))
    }

    @Test
    fun `isPast respects the tolerance window`() {
        val justNow = Clock.nowRfc3339()
        // 刚过去的时刻在容差内不算"已过期"：容差存在的意义就是吸收网络往返与时钟偏差
        assertFalse(Clock.isPast(justNow, toleranceMs = 5_000))
        // 容差为负时同一时刻立刻算过期，证明判定确实走了时间比较
        assertTrue(Clock.isPast(justNow, toleranceMs = -1))
    }

    @Test
    fun `isPast treats unparseable input as not yet past`() {
        // 解析不出来的时刻宁可继续处理：因为一个不认识的格式把正常请求全丢掉，
        // 比多处理几条已经过期的更糟
        assertFalse(Clock.isPast("", toleranceMs = 0))
        assertFalse(Clock.isPast("garbage", toleranceMs = 0))
    }
}
