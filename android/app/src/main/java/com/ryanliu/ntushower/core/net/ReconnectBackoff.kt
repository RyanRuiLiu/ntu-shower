package com.ryanliu.ntushower.core.net

import kotlin.random.Random

/**
 * 重连退避计算（纯逻辑，可单测）。
 *
 * 指数退避封顶，再加**等量抖动**：实际等待落在各级上限的一半到上限之间。抖动是
 * 几十台设备的规模下才显出必要的一层——共用一次断网（服务器重启、楼内网络闪断）
 * 的设备们退避计数几乎同步，没有它就会永远以同一波次同时重连，每一波都是 N 条
 * 并发 TLS 握手加 hello 处理同时砸向服务端；抖动把同一波的设备摊开到半个退避
 * 窗口里，任何一台设备的平均等待仍是同阶的量。
 */
object ReconnectBackoff {

    /** 指数增长的最大移位位数：2^4 = 16 倍于基址。 */
    const val MAX_SHIFT = 4

    fun delayMs(attempt: Int, baseMs: Long, maxMs: Long): Long = delayMs(attempt, baseMs, maxMs, Random.Default)

    /**
     * 第 [attempt] 次失败后的等待时长（attempt 从 1 开始）。
     *
     * 封顶值 = baseMs × 2^min(attempt-1, MAX_SHIFT) 与 maxMs 取小；[random] 决定
     * 实际落点在封顶值的哪一半。随机源可注入，单测据此钉死边界。
     */
    fun delayMs(attempt: Int, baseMs: Long, maxMs: Long, random: Random): Long {
        val shift = minOf(attempt - 1, MAX_SHIFT).coerceAtLeast(0)
        val cap = minOf(baseMs shl shift, maxMs)
        return cap / 2 + random.nextLong(cap - cap / 2 + 1)
    }
}
