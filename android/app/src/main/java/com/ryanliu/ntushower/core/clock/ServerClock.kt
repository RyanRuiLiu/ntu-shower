package com.ryanliu.ntushower.core.clock

import java.util.Date

/**
 * 服务端时间：把本机单调钟锚在服务端下发的时刻上，给出"服务端时间轴上的此刻"。
 *
 * ## 为什么不能读墙钟
 *
 * 帧的产生时刻必须**落在服务端的时间轴上**——两端时钟不同源，会话时长与时段统计就
 * 无处对齐。而本机墙钟不可用：这台设备没有 NTP，实测 100 天里它与服务端的偏差从
 * -24 小时到 +24 小时（p90 就是整整一小时），运维会在深夜回拨以推后浴室 App 的自动
 * 释放，此外另有 8.5 秒/天的自然漂移。
 *
 * 修法不是"把墙钟改对"。那是 [com.ryanliu.ntushower.features.selfmanage.TimeSync] 的
 * 职责，要 root 改系统时钟，且是**可选项**（`boot_time_sync_enabled` 默认关闭）——不能
 * 当作时刻来源的前提。改成**只用本机量增量、锚点交给服务端**：
 *
 * - 服务端每一轮 `link.ping` 都带 `sender_time`，握手应答里另带 `server_time`；
 * - [com.ryanliu.ntushower.transport.GatewayClient] 收到就调 [anchor]，记下"那一刻的
 *   服务端时刻"与"那一刻的本机单调钟读数"；
 * - 此后 `锚点时刻 + 单调钟走过的毫秒数` 就是当前的服务端时刻。
 *
 * 墙钟被改成什么都不影响——它一个字节都不参与运算。
 *
 * ## 与相邻两件事的分工
 *
 * - [Clock]：**协议时间**——墙上时钟，负责格式与解析；
 * - [Uptime]：**本机经过时间**——单调钟，判超时、算耗时，只在本机内部有意义；
 * - [ServerClock]：**服务端时间轴上的此刻**——同样用单调钟量增量，但起算点由服务端
 *   给出，因此拿出去能被对端理解。这是它与 [Uptime] 唯一的区别，也是它存在的理由。
 *
 * [Uptime] 那条"绝不外发"的约束在这里依然成立：外发的是锚在服务端上的真实时刻，
 * 单调钟只在内部量增量，一个字节都不出去。
 *
 * ## 精度
 *
 * 锚点之间最多漂移一个 ping 周期的晶振误差（默认 60 秒，几十 ppm 即毫秒量级），再加
 * 服务端到本机的单向延迟（同一网络下几十毫秒）。合计远小于一秒，而本系统的时间刻度
 * 以秒计。
 *
 * ## 没有锚点时
 *
 * [nowRfc3339] 返回 null，调用方据此**不携带**该字段，由服务端退回本机接收时刻。设备
 * 刚起来、还没握上手时是这种状态，通常只持续到第一帧 `link.hello_ack`。
 */
object ServerClock {

    /** 锚定的服务端时刻（Unix 毫秒）；0 表示尚无锚点。 */
    @Volatile
    private var anchorServerMs: Long = 0

    /** 锚定那一刻的本机单调钟读数（毫秒）。 */
    @Volatile
    private var anchorUptimeMs: Long = 0

    /**
     * 用服务端下发的时刻重新锚定。握手应答与每一轮 ping 都调，锚点因此始终新鲜。
     *
     * 解析不出格式就保持原锚点不动：宁可用一个稍旧的锚点，也不能让坏值把时间轴带偏。
     */
    fun anchor(serverTimeRfc3339: String) {
        val moment = Clock.parseRfc3339(serverTimeRfc3339) ?: return
        anchorServerMs = moment.time
        anchorUptimeMs = Uptime.nowMs()
    }

    /** 当前的服务端时刻；尚无锚点时返回 null。 */
    fun nowRfc3339(): String? {
        val serverMs = anchorServerMs
        if (serverMs == 0L) return null
        return Clock.formatRfc3339(Date(serverMs + (Uptime.nowMs() - anchorUptimeMs)))
    }
}
