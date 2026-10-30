package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.TimeZone
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ClockTest {

    @Test
    fun `format is utc rfc3339 with millisecond precision`() {
        val timestamp = Clock.wallClockRfc3339()
        assertTrue("missing Z suffix: $timestamp", timestamp.endsWith("Z"))

        // 可被反向解析且时区语义为 UTC（用独立的解析器验证，不依赖被测类提供解析）
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
                    val ts = Clock.wallClockRfc3339()
                    if (ts.length != 24 || !ts.endsWith("Z")) {
                        errors.incrementAndGet()
                    }
                }
                done.countDown()
            }.start()
        }
        assertTrue("八个线程都应在限时内结束", done.await(5, TimeUnit.SECONDS))
        assertEquals(0, errors.get())
    }

    @Test
    fun `parse round-trips the formatted output`() {
        // 解析的消费者是 ServerClock（拿 server_time 立锚点），输入就是本类的输出
        val now = Clock.wallClockRfc3339()
        val parsed = Clock.parseRfc3339(now)

        assertTrue(parsed != null)
        assertEquals(now, Clock.formatRfc3339(parsed!!))
    }

    @Test
    fun `parse returns null for malformed input`() {
        // 毫秒缺失、非法文本都拒绝：调用方据此走留痕分支，而不是拿到错误的时刻
        assertTrue(Clock.parseRfc3339("2026-10-02T05:11:14Z") == null)
        assertTrue(Clock.parseRfc3339("not-a-time") == null)
        assertTrue(Clock.parseRfc3339("") == null)
    }
}
