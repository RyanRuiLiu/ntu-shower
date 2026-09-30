package com.ryanliu.ntushower.core.net

/**
 * 重连退避计算（纯逻辑，可单测）。
 *
 * 指数退避封顶：失败次数再多也不能让重连间隔无限增长，否则设备会长时间失联。
 */
object ReconnectBackoff {

    /** 指数增长的最大移位位数：2^4 = 16 倍于基址。 */
    const val MAX_SHIFT = 4

    /**
     * 第 [attempt] 次失败后的等待时长（attempt 从 1 开始）。
     *
     * baseMs × 2^min(attempt-1, MAX_SHIFT)，结果不超过 maxMs。
     */
    fun delayMs(attempt: Int, baseMs: Long, maxMs: Long): Long {
        val shift = minOf(attempt - 1, MAX_SHIFT).coerceAtLeast(0)
        return minOf(baseMs shl shift, maxMs)
    }
}
