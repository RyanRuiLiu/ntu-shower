package com.ryanliu.ntushower.core.clock

import android.os.SystemClock

/**
 * 单调时钟：距开机经过的毫秒数。
 *
 * ## 凡是"过了多久"一律走这里
 *
 * 不要用 `System.currentTimeMillis` 算超时。这台设备没有 NTP，墙上时钟会被运维改动、掉电
 * 重启后也会跳变——用它判超时，时钟被往回调会让等待无声地变长，往前调则直接提前失败，而
 * 两种都表现为"偶发"，归因不到时钟上。设备的现状是**读的时间可能是错的**，这一点
 * [com.ryanliu.ntushower.core.clock.Clock] 里已经为协议时间记过一次。
 *
 * 取 [SystemClock.elapsedRealtime] 而不是 `System.nanoTime`：后者不含深度睡眠时间，而
 * "过了多久"该把休眠算进去。设备是常亮终端，两者眼下等价，但语义上要的是前者。
 *
 * ## 与相邻两件事的分工
 *
 * - [Clock]：**协议时间**——给对方看的时刻，必须与真实世界一致，所以它用墙上时钟；
 * - [Uptime]：**本地经过时间**——只在判定超时时有意义，绝不外发（两端时钟不同源，
 *   发出去就是给对端制造一个陷阱）；
 * - `System.nanoTime`：**单段代码的耗时**——比如某次遍历花了多少毫秒。它只在一处进出、
 *   基准无所谓，因此不并入这里。
 */
object Uptime {

    /** 距开机经过的毫秒数。 */
    fun nowMs(): Long = SystemClock.elapsedRealtime()
}
