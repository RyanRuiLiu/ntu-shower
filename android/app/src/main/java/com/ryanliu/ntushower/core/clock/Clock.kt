package com.ryanliu.ntushower.core.clock

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 设备侧的 RFC 3339 时间格式化。
 *
 * 对应协议信封的 `ts`——两端的时间字符串都经由此处，格式不会各自漂移。
 *
 * **只有格式化，没有解析**：设备一旦需要"判断某个时刻过了没有"就会依赖两台机器的
 * 时钟一致，而这台设备没有 NTP。命令的有效期因此改用相对时长下发（见
 * `protocol/schema/session.command.json` 的 `timeout_ms`），设备侧不再有解析对端时刻
 * 的需求。留着解析只会诱使后来者再去比绝对时刻。
 */
object Clock {

    // SimpleDateFormat 非线程安全：心跳/自动化/主线程并发调用，故 ThreadLocal 隔离
    private val formatter = object : ThreadLocal<SimpleDateFormat>() {
        override fun initialValue(): SimpleDateFormat =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
    }

    fun nowRfc3339(): String = formatRfc3339(Date())

    /**
     * 把给定的时刻格式化为协议时间。格式串因此只在本文件出现一份：浴室 App 的本地
     * 时间转协议时间（features.doors 的 DoorRaw）用的就是这里。解析依然不设，理由见类注释。
     */
    fun formatRfc3339(date: Date): String = formatter.get()!!.format(date)
}
