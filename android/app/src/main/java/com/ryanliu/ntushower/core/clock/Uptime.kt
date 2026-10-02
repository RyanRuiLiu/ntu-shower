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
 * ## 与相邻三件事的分工
 *
 * - [Clock]：**协议时间**——给对方看的时刻，必须与真实世界一致，所以它用墙上时钟；
 * - [Uptime]：**本地经过时间**——判定超时、计算分段耗时、给落盘日志打时间戳都走这里；
 *   它只在本机内部有意义，绝不外发（两端时钟不同源，发出去就是给对端制造一个陷阱）；
 * - [ServerClock]：**服务端时间轴上的此刻**——同样用单调钟量增量，但起算点由服务端
 *   给出，因此可以外发。它是"要对端理解"与"不受墙钟改动影响"这两个要求的交叉点；
 * - `System.nanoTime`：不是时间源。它不含深度睡眠时间，语义上输给
 *   [SystemClock.elapsedRealtime]；仅存的使用是 ShellChannel 的标记戳——那要的是
 *   唯一性，不是时刻。
 */
object Uptime {

    /** 距开机经过的毫秒数。 */
    fun nowMs(): Long = SystemClock.elapsedRealtime()
}
