package com.ryanliu.ntushower.core.clock

import android.os.SystemClock

/**
 * 单调钟：距开机经过的毫秒数。
 *
 * ## 凡是"过了多久"一律走这里
 *
 * 不要用 `System.currentTimeMillis` 算超时：设备的墙上时钟会被运维改动、掉电重启后也
 * 会跳变（[Clock] 与 [ServerClock] 的类注释各记过一次背景）。用它判超时，往回调会让
 * 等待无声变长，往前调则直接提前失败，两种都表现为"偶发"，归因不到时钟上。设备的
 * 现状是**读的时间可能是错的**。
 *
 * 取 [SystemClock.elapsedRealtime] 而不是 `System.nanoTime`：后者不含深度睡眠时间
 * （Android 官方文档），而"过了多久"该把休眠算进去。设备常亮，两者眼下等价，语义上
 * 要的是前者。真机现存三处 `nanoTime`——ShellChannel 的唯一标记戳（要的是唯一性，
 * 不是时刻）、WaterBoardClient 与 VoicePcmDecoder 的阻塞读超时——分属命令通道与
 * 外设/语音域，按各自的重写批次归位；本对象仍是"过了多久"的唯一入口。
 *
 * ## 与相邻两件事的分工
 *
 * - [Clock]：**协议时间**——给设备侧格式与解析用的墙上时钟读数；
 * - [Uptime]：**本地经过时间**——判定超时、计算分段耗时、给落盘日志打时间戳都走这里；
 *   它只在本机内部有意义，**绝不外发**——两端时钟不同源，发出去就是给对端制造一个陷阱；
 * - [ServerClock]：**服务端时间轴上的此刻**——同样用单调钟量增量，但起算点由服务端
 *   给出，因此可以外发。
 */
object Uptime {

    /** 距开机经过的毫秒数。 */
    fun nowMs(): Long = SystemClock.elapsedRealtime()
}
