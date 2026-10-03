package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.logfile.CappedLogFile
import java.io.File

/**
 * 自动化流程的分段耗时记录。
 *
 * 存在的理由：这套流程的瓶颈在哪，光读代码看不出来——每个等待都是「事件驱动 + 上限」，
 * 实际花多久取决于浴室 App 什么时候给出界面变化，只能靠一段真实的运行记录来定位。
 *
 * 输出到 `/sdcard/ntu-shower/automation.log`，用 `device.exec` 直接 `cat` 取回。现场没有
 * logcat，这是把设备上的时序带回开发机的唯一通道（崩溃日志走的是同一条路）。
 *
 * 时间基准用单调时钟（[Uptime]，开机以来的毫秒数）：设备没有 NTP，墙上时钟会被运维
 * 改动、掉电重启后跳变，用它算分段会算出负数。落盘的绝对时刻与 device.log 的
 * `uptime_ms` 同源，两份日志能按时间对上；协议时间的分工见 core/clock。
 *
 * 由 `automation_trace_enabled` 开关控制，默认关——每次记录都要开一次文件，持续开启
 * 是无谓的开销，而它的用途本就是"要量的时候量一轮"。
 */
object AutomationTrace {

    private const val TAG = "AutomationTrace"

    private const val FILE_NAME = "automation.log"

    /**
     * 页面重算统计的落盘间隔（次）。
     *
     * 逐条落盘等于每次重算都开关一次文件——记录本身成了被测对象，量出来的数也就不作数了。
     * 攒够这些次落一行，写盘量降两个数量级，而拿到的还是那几个数：重算有多密、每次多久、
     * 几次真的换了页。
     */
    private const val REFRESH_FLUSH_EVERY = 50

    private val refreshWindow = RefreshWindow(REFRESH_FLUSH_EVERY)

    /**
     * 开始记录一轮流程；开关关闭时返回 null。
     *
     * 调用方拿到 null 就什么都不用做，各标记点写成 `trace?.mark(...)` 即可——不要为了
     * 记录而给流程加分支，那会让"关掉记录"和"打开记录"跑的不是同一套代码。
     */
    fun begin(label: String): Trace? {
        if (!RuntimeConfig.automationTraceEnabled) return null
        return Trace(label)
    }

    /** 一轮流程的分段记录器。 */
    class Trace(private val label: String) {

        private val startedAt = Uptime.nowMs()
        private var lastAt = startedAt

        /** 记一个分段：输出距上一段的耗时与累计耗时。 */
        fun mark(step: String) {
            val now = Uptime.nowMs()
            append("$label $step +${now - lastAt}ms (累计 ${now - startedAt}ms)")
            lastAt = now
        }

        /** 收尾：输出总耗时。之后不应再调用 [mark]。 */
        fun end(outcome: String) {
            val now = Uptime.nowMs()
            append("$label end=$outcome total=${now - startedAt}ms")
        }
    }

    /**
     * 记一次页面状态重算。
     *
     * 这条记录回答三个问题：**重算有多密**（`span/n`）、**每次要花多久**（`avg` 与 `max`）、
     * 以及**这么多次里有几次真的换了页**（`changed`）。
     *
     * 第三个是筛掉噪声事件之后最该看的数：事件本身有多少条、分别来自谁，已经由
     * [PageEventRules] 在入口处挡掉，不必在这里再记一遍。
     */
    fun recordRefresh(costMs: Long, state: String, changed: Boolean) {
        if (!RuntimeConfig.automationTraceEnabled) return
        val line = refreshWindow.add(costMs, state, changed, Uptime.nowMs()) ?: return
        append(line)
    }

    /**
     * 记一次"取页面特征"所花的时间（毫秒）。
     *
     * 一段识别的耗时里，取根节点与取特征是两笔来历完全不同的开销：前者是系统在活动窗口上
     * 取一次节点树，与树有多大无关（实测 35ms）；后者是把取回来的树走一遍、收成资源 ID 到
     * 文本的表（实测约 6ms）。只记总耗时就看不出该砍哪一段——这一轮正是靠分开记，才发现
     * "交给系统按 ID 查"看着更省、实则是另一次系统调用，每次多花 26ms。
     *
     * 只累加在内存里，攒够一批才随 [recordRefresh] 一起落盘，所以调用频率高不要紧。
     */
    fun recordQueryTime(ms: Long) {
        if (!RuntimeConfig.automationTraceEnabled) return
        refreshWindow.addQuery(ms)
    }

    private fun append(text: String) {
        Log.d(TAG, text)
        val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
        CappedLogFile.append(File(dir, FILE_NAME), "[${Uptime.nowMs()}] $text\n", CappedLogFile.DEFAULT_MAX_BYTES)
    }
}

/**
 * 页面重算的滑动统计：攒够一批落一行。
 *
 * 为什么不逐条记流水，见 [AutomationTrace] 里 `REFRESH_FLUSH_EVERY` 的说明。这里只留能回答
 * 问题的几个数——总数、跨度、均值、峰值、查询那一段的均值与峰值、真换页的次数、以及最后
 * 停在哪一页。
 */
internal class RefreshWindow(private val flushEvery: Int) {

    private var count = 0
    private var changedCount = 0
    private var totalMs = 0L
    private var maxMs = 0L
    private var queryTotalMs = 0L
    private var queryMaxMs = 0L
    private var openedAtMs = 0L
    private var lastState = ""

    /** 累加一次按 ID 查询的耗时；由 [AutomationTrace.recordQueryTime] 转发。 */
    @Synchronized
    fun addQuery(ms: Long) {
        queryTotalMs += ms
        if (ms > queryMaxMs) queryMaxMs = ms
    }

    /** 记一次重算；攒够一批时返回要落的记录（可能多行），否则返回 null。 */
    @Synchronized
    fun add(costMs: Long, state: String, changed: Boolean, nowMs: Long): String? {
        if (count == 0) openedAtMs = nowMs
        count++
        if (changed) changedCount++
        totalMs += costMs
        if (costMs > maxMs) maxMs = costMs
        lastState = state
        if (count < flushEvery) return null
        val line = "refresh n=$count span=${nowMs - openedAtMs}ms avg=${totalMs / count}ms " +
            "max=${maxMs}ms qavg=${queryTotalMs / count}ms qmax=${queryMaxMs}ms " +
            "changed=$changedCount last=$lastState"
        count = 0
        changedCount = 0
        totalMs = 0
        maxMs = 0
        queryTotalMs = 0
        queryMaxMs = 0
        return line
    }
}
