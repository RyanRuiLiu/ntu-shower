package com.ryanliu.ntushower.core.clock

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/** 设备侧时间输出：UTC RFC 3339 毫秒精度（对应协议信封 ts 字段）。 */
object Clock {

    // SimpleDateFormat 非线程安全：心跳/自动化/主线程并发调用，故 ThreadLocal 隔离
    private val formatter = object : ThreadLocal<SimpleDateFormat>() {
        override fun initialValue(): SimpleDateFormat =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
    }

    fun nowRfc3339(): String = formatter.get()!!.format(Date())
}
