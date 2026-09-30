package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
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
 * 时间基准用单调时钟（`System.nanoTime`）而不是墙上时钟：设备没有 NTP，系统时间会被
 * 运维改动、掉电重启后也会跳变，用墙上时钟算分段会算出负数。
 *
 * 由 `automation_trace_enabled` 开关控制，默认关——它每次 append 都要开一次文件，长期
 * 开着是白花的开销，而它的用途本来就是"要量的时候量一轮"。
 */
object AutomationTrace {

    private const val TAG = "AutomationTrace"

    private const val FILE_NAME = "automation.log"

    /** 超过这个大小就重写：记录的价值在最近几轮，不设上限会在反复运行中撑爆存储。 */
    private const val MAX_BYTES = 256 * 1024L

    /**
     * 页面重算统计的落盘间隔（次）。
     *
     * 重算每秒发生十几次（浴室 App 首页的跑马灯与时钟驱动的），逐条落盘就是每秒开关十几次
     * 文件——记录本身成了被测对象，量出来的数也就不作数了。攒够这些次落一行，拿到的是同样
     * 几个数（事件有多密、每次多久、几次真的换了页），写盘量降两个数量级。
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

        private val startedAt = System.nanoTime() / 1_000_000
        private var lastAt = startedAt

        /** 记一个分段：输出距上一段的耗时与累计耗时。 */
        fun mark(step: String) {
            val now = System.nanoTime() / 1_000_000
            append("$label $step +${now - lastAt}ms (累计 ${now - startedAt}ms)")
            lastAt = now
        }

        /** 收尾：输出总耗时。之后不应再调用 [mark]。 */
        fun end(outcome: String) {
            val now = System.nanoTime() / 1_000_000
            append("$label end=$outcome total=${now - startedAt}ms")
        }
    }

    /**
     * 记一次内容变化事件的来源。
     *
     * 与 [recordRefresh] 记的是两件事：那个记"这次重算花了多久、算出来有没有变"，这个记
     * "事件自己长什么样"。两个答案要能对上——一个是分母（来了多少事件），一个是分子（几次
     * 真换了页）。跑马灯与时钟贡献的分母最大，而分子恒为零。
     *
     * 它由无障碍事件回调在**主线程**上调用，所以只往一张内存表加个计数，不碰磁盘。
     */
    internal fun recordEvent(event: ContentEvent) {
        if (!RuntimeConfig.automationTraceEnabled) return
        refreshWindow.addEvent(event)
    }

    /**
     * 记一次页面状态重算。
     *
     * 这条记录要回答三个问题：**界面事件来得有多密**（`span/n` 就是事件间隔）、**每次重算
     * 要花多久**（`avg` 与 `max`）、以及**这么多次里有几次真的换了页**（`changed`）。前两个
     * 决定事件驱动这套选择的代价，第三个决定"不做节流"站不站得住。
     */
    fun recordRefresh(costMs: Long, state: String, changed: Boolean) {
        if (!RuntimeConfig.automationTraceEnabled) return
        val line = refreshWindow.add(costMs, state, changed, System.nanoTime() / 1_000_000) ?: return
        append(line)
    }

    /**
     * 记一次"取页面特征"所花的时间（毫秒）。
     *
     * 一段识别的耗时里，取根节点与取特征是两笔来历完全不同的开销：前者是系统在活动窗口上
     * 做一次快照，与树有多大无关（实测 35ms）；后者是把取回来的树走一遍、收成资源 ID 到
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
        // 整段包在 try 里：记录跑在自动化流程的关键路径上，它自己把流程搞挂就本末倒置了。
        // 写文件会失败（路径不可写、存储满），日志调用在单测里也会失败。
        try {
            Log.d(TAG, text)
            val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
            val file = File(dir, FILE_NAME)
            if (file.length() > MAX_BYTES) file.delete()
            // 一条记录可能不止一行（汇总行后面跟着事件来源分布），逐行带时间戳便于按时间对齐
            val stamp = System.nanoTime() / 1_000_000
            file.appendText(text.split('\n').joinToString("") { "[$stamp] $it\n" })
        } catch (_: Exception) {
            // 记录是"顺便"的，失败了不该影响主流程
        }
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

    /**
     * 本窗口内各来源的事件条数，键是 [ContentEvent.describe]。
     *
     * 种类数要有上限：事件的文本字段可能带倒计时、流水号这类每次都不同的内容，不设上限
     * 这张表会随运行时长一直长下去。
     */
    private val eventCounts = HashMap<String, Int>()
    private var overflowed = 0

    /** 累加一次按 ID 查询的耗时；由 [AutomationTrace.recordQueryTime] 转发。 */
    @Synchronized
    fun addQuery(ms: Long) {
        queryTotalMs += ms
        if (ms > queryMaxMs) queryMaxMs = ms
    }

    /** 累加一次事件来源；由 [AutomationTrace.recordEvent] 转发，在**主线程**上调用。 */
    @Synchronized
    fun addEvent(event: ContentEvent) {
        val key = event.describe()
        val seen = eventCounts[key]
        when {
            seen != null -> eventCounts[key] = seen + 1
            eventCounts.size < MAX_EVENT_KINDS -> eventCounts[key] = 1
            else -> overflowed++
        }
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
        val summary = "refresh n=$count span=${nowMs - openedAtMs}ms avg=${totalMs / count}ms " +
            "max=${maxMs}ms qavg=${queryTotalMs / count}ms qmax=${queryMaxMs}ms " +
            "changed=$changedCount last=$lastState"
        val breakdown = breakdown()
        count = 0
        changedCount = 0
        totalMs = 0
        maxMs = 0
        queryTotalMs = 0
        queryMaxMs = 0
        eventCounts.clear()
        overflowed = 0
        return if (breakdown.isEmpty()) summary else "$summary\n$breakdown"
    }

    /**
     * 本窗口的事件来源分布，按条数降序。
     *
     * 只列前面几种：要回答的是"谁在撑着这个分母"，而噪声源通常就一两种。
     */
    private fun breakdown(): String {
        if (eventCounts.isEmpty()) return ""
        val top = eventCounts.entries.sortedByDescending { it.value }.take(MAX_EVENT_KINDS_IN_LINE)
        val lines = top.joinToString("\n") { entry ->
            // 这里不能图省事写 "$it.value× $it.key"：$it 会整个展开成 Map.Entry 的
            // "键=值"，跟在后头的 ".value" 就变成原样打进日志的字面量。解构之后两个都是简单
            // 变量，也就不必用花括号去界定边界。
            val (key, count) = entry
            "  ev $count× $key"
        }
        val rest = eventCounts.size - top.size
        return if (rest == 0 && overflowed == 0) {
            lines
        } else {
            "$lines\n  ev 其余 $rest 种，溢出 $overflowed 条"
        }
    }

    private companion object {
        /** 每个窗口最多列出几种来源。 */
        const val MAX_EVENT_KINDS_IN_LINE = 6

        /** 一个窗口最多记住几种来源，超出的只计数。 */
        const val MAX_EVENT_KINDS = 64
    }
}
