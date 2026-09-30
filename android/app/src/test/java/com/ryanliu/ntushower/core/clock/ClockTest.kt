package com.ryanliu.ntushower.core.clock

import org.junit.Assert.assertEquals
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
}
