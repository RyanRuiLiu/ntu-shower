package com.ryanliu.ntushower.core.clock

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 设备侧的 RFC 3339 时间格式化与解析。
 *
 * 对应协议信封的 `ts`——两端的时间字符串都经由此处，格式不会各自漂移。
 *
 * 解析的消费者是 [ServerClock]：拿 `link.hello_ack` 的 `server_time` 与每一轮
 * `link.ping` 的 `sender_time` 立锚点。**墙钟本身仍不得用于"判断某个时刻
 * 过了没有"**——那类判定依赖两台机器的时钟一致，而这台设备没有 NTP；命令的有效期因此
 * 仍是相对时长（见 `protocol/schema/session.command.json` 的 `timeout_ms`）。
 *
 * **命名带 `wallClock` 是刻意的**：这里和 [ServerClock] 都能给"现在"，但一个是设备那口
 * 会被拨的钟、一个是锚在服务端轴上的权威时刻，差一个 3590 秒的偏差。名字里不写出轴，
 * 调用方只看函数名分不出拿的是哪条——而分错是静默的。
 */
object Clock {

    // SimpleDateFormat 非线程安全：心跳/自动化/主线程并发调用，故 ThreadLocal 隔离
    private val formatter = object : ThreadLocal<SimpleDateFormat>() {
        override fun initialValue(): SimpleDateFormat =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
    }

    /** 设备墙钟此刻（**不是服务端轴**；要权威时刻用 [ServerClock.nowRfc3339]）。 */
    fun wallClockRfc3339(): String = formatRfc3339(Date())

    /**
     * 把给定的时刻格式化为协议时间。格式串因此只在本文件出现一份：浴室 App 的本地
     * 时间转协议时间（features.doors 的 DoorRaw）用的就是这里。
     */
    fun formatRfc3339(date: Date): String = formatter.get()!!.format(date)

    /**
     * 解析服务端输出的 RFC 3339 毫秒字符串（Z 后缀，与服务端 core/clock.py 的输出
     * 同型）。格式不符返回 null，由调用方决定是否留痕——解析失败的处置是调用方的
     * 业务语义，这里不记日志（core 层不依赖任何日志设施）。
     */
    fun parseRfc3339(value: String): Date? = try {
        formatter.get()!!.parse(value)
    } catch (e: Exception) {
        null
    }
}
