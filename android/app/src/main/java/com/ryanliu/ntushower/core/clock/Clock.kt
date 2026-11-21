package com.ryanliu.ntushower.core.clock

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * 设备侧的 RFC 3339 格式化与解析：协议信封的 `ts` 与帧载荷里的时间字段都经由此处，
 * 全项目只有这一份格式实现。
 *
 * [formatRfc3339]/[parseRfc3339] 是服务端 `app/core/clock.py` 的对侧，两端输出同型
 * （UTC、毫秒、`Z` 后缀）。解析的消费者是 [ServerClock]——拿 `link.hello_ack` 的
 * `server_time` 与每一轮 `link.ping` 的 `sender_time` 立锚点；格式化的另一个消费者是
 * 浴室 App 本地时刻转协议时间（features.doors 的 DoorRaw）。
 *
 * **墙钟本身不得用于"判断某个时刻过了没有"**——那类判定依赖两台机器的时钟一致，而这
 * 台设备没有 NTP；命令的有效期因此仍是相对时长（见 `protocol/schema/device.command.json`
 * 的 `timeout_ms`）。读墙上钟只回答"设备此刻的读数是什么"这一种事实（偏差测量、与
 * 浴室 App 同源的字段），"过了多久"走 [Uptime]，外发时刻走 [ServerClock]。
 *
 * `SimpleDateFormat` 非线程安全（Java 官方文档明示），心跳、自动化、主线程会并发调用，
 * 故访问以 `@Synchronized` 串行——调用频率是每帧毫秒级，监视器开销可忽略。Android 4.4
 * 没有 `java.time`，它是本 API 级别下的标准选择。命名见 [wallClockRfc3339]：三个对象
 * 都能给"现在"，名字不写出轴，调用方就分不出拿的是哪一条——而分错是静默的。
 */
object Clock {

    /** 本系统的 RFC 3339 形态：UTC、毫秒精度、`Z` 后缀（RFC 3339 §5.6）。 */
    private const val PATTERN = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"

    private val formatter = SimpleDateFormat(PATTERN, Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }

    /** 设备墙上时钟此刻的读数（**不是服务端轴**；权威时刻用 [ServerClock.nowRfc3339]）。 */
    @Synchronized
    fun wallClockRfc3339(): String = formatRfc3339(Date())

    /**
     * 把给定时刻格式化为协议时间。浴室 App 的本地时间（`t_doorlist` 的
     * `yyyyMMddHHmmss`）转协议时间走的也是这里。
     */
    @Synchronized
    fun formatRfc3339(date: Date): String = formatter.format(date)

    /**
     * 解析服务端输出的 RFC 3339 毫秒字符串。格式不符返回 null，处置是调用方的业务
     * 语义（core 层不依赖日志设施，不在此留痕）。
     */
    @Synchronized
    fun parseRfc3339(value: String): Date? = try {
        formatter.parse(value)
    } catch (e: Exception) {
        null
    }
}
