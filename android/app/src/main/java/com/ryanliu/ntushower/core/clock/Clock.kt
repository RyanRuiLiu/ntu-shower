package com.ryanliu.ntushower.core.clock

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 设备侧的 RFC 3339 时间：格式化与解析的唯一入口。
 *
 * 与协议信封的 `ts`、`session.command` 的 `deadline_ts` 对应。两端的时间字符串
 * 都经由此处，格式不会各自漂移。
 */
object Clock {

    // SimpleDateFormat 非线程安全：心跳/自动化/主线程并发调用，故 ThreadLocal 隔离
    private val formatter = object : ThreadLocal<SimpleDateFormat>() {
        override fun initialValue(): SimpleDateFormat =
            SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }
    }

    fun nowRfc3339(): String = formatter.get()!!.format(Date())

    /**
     * 解析 RFC 3339 字符串为 epoch 毫秒；无法解析时返回 null。
     *
     * 入参可能是服务端给的任何东西（`deadline_ts` 来自对端），所以格式不合法必须
     * 在这一层吸收成 null，而不是抛到调用栈上。
     */
    fun parseRfc3339(text: String): Long? {
        if (text.isEmpty()) return null
        return try {
            formatter.get()!!.parse(text)?.time
        } catch (e: Exception) {
            // 格式不符（时区偏移写法、秒以下位数不同等）一律视为不可解析
            null
        }
    }

    /**
     * 给定时刻是否已经过去超过 `toleranceMs`。
     *
     * 容差是必须的：那个时刻是对端按**它的**时钟算出来的，网络往返加上两端时钟偏差，
     * 一条其实还来得及的命令会落在边界外。
     *
     * 无法解析时返回 false（当作"还没过期"）：那说明对端给了个不认识的格式，宁可
     * 继续处理，也不要因为解析失败就把正常请求全丢掉。
     */
    fun isPast(text: String, toleranceMs: Long): Boolean {
        val at = parseRfc3339(text) ?: return false
        return System.currentTimeMillis() - at > toleranceMs
    }
}
