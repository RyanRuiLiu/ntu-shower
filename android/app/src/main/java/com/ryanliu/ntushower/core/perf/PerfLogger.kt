package com.ryanliu.ntushower.core.perf

import android.util.Log

/** 性能计时埋点：关键路径耗时统一记录（读取/自动化各步骤）。 */
object PerfLogger {

    private const val TAG = "Perf"

    /** 超过此耗时的记录以 WARN 级别输出，便于在 logcat 中快速筛出慢路径。 */
    private const val SLOW_MS = 200L

    fun log(tag: String, name: String, elapsedMs: Long) {
        if (elapsedMs >= SLOW_MS) {
            Log.w(TAG, "SLOW $tag/$name: ${elapsedMs}ms")
        } else {
            Log.i(TAG, "$tag/$name: ${elapsedMs}ms")
        }
    }
}
