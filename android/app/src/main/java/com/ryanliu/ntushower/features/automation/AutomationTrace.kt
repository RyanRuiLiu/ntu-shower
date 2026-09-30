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
     * 记一次页面状态重算。
     *
     * 这条记录要回答两个问题：**界面事件来得有多密**（行与行的时间差就是事件间隔），
     * 以及**每次重算要花多久**（遍历那棵树的真实代价）。两个数一起决定"事件驱动、
     * 不节流"这个选择是不是站得住。
     *
     * 只在开关打开时写，否则每秒一两行会持续堆积。
     */
    fun recordRefresh(costMs: Long, state: String) {
        if (!RuntimeConfig.automationTraceEnabled) return
        append("page $state cost=${costMs}ms")
    }

    private fun append(line: String) {
        // 整段包在 try 里：记录跑在自动化流程的关键路径上，它自己把流程搞挂就本末倒置了。
        // 写文件会失败（路径不可写、存储满），日志调用在单测里也会失败。
        try {
            Log.d(TAG, line)
            val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
            val file = File(dir, FILE_NAME)
            if (file.length() > MAX_BYTES) file.delete()
            file.appendText("[${System.nanoTime() / 1_000_000}] $line\n")
        } catch (_: Exception) {
            // 记录是"顺便"的，失败了不该影响主流程
        }
    }
}
