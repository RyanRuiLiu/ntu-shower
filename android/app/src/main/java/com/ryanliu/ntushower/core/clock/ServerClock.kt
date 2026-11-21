package com.ryanliu.ntushower.core.clock

import java.util.Date

/**
 * 服务端时间：把本机单调钟锚在服务端下发的时刻上，给出服务端时间轴上的此刻。
 *
 * ## 为什么不能读墙钟
 *
 * 外发的时刻必须落在服务端的时间轴上——两端时钟不同源，会话时长与时段统计就无处对齐。
 * 而本机墙钟不可用：设备没有 NTP，实测 100 天里它与服务端的偏差从 -24 小时到 +24 小时
 * （p90 就是整整一小时），运维会在深夜回拨以推后浴室 App 的自动释放，此外另有
 * 8.5 秒/天的自然漂移。修法不是"把墙钟改对"——那是 TimeSync 的职责，要 root 改系统
 * 时钟，且是可选项（`time_sync_enabled` 默认关闭），不能当作时刻来源的前提。
 *
 * 由此只用本机量增量、锚点交给服务端：服务端每一轮 `link.ping` 都带 `sender_time`，
 * 握手应答另带 `server_time`；GatewayClient 收到即调 [anchor]，记下"那一刻的服务端
 * 时刻"与"那一刻的本机单调钟读数"。此后 `锚点时刻 + 单调钟走过的毫秒数` 就是当前的
 * 服务端时刻。墙钟被改成什么都不影响——它一个字节都不参与运算。
 *
 * ## 并发形态：单引用整体交换
 *
 * 锚点是一对必须成对使用的读数（服务端时刻 + 当刻单调钟读数）。把它装进不可变的
 * [Anchor]，以**单个** `@Volatile` 引用整体交换：写入与读取各自原子，且引用指向的
 * 对象一经构造不再变化（JLS §17.5 final 字段语义），并发交错拿到的永远是成对自洽的
 * 一组值。若拆成两个 `@Volatile` 字段，`nowRfc3339` 可能读到"旧时刻配新读数"的交错，
 * 算出负增量——单引用交换就是为排除这个而存在的。
 *
 * ## 与相邻两件事的分工
 *
 * - [Clock]：**协议时间**——设备墙上时钟的读数与格式化；
 * - [Uptime]：**本机经过时间**——单调钟，判超时、算耗时，只在本机内部有意义；
 * - [ServerClock]：**服务端时间轴上的此刻**——与 [Uptime] 同样量增量，差别只在起算点
 *   由服务端给出，因此拿出去能被对端理解。`Uptime` 的"绝不外发"在此依然成立：外发的
 *   是锚在服务端上的时刻，单调钟只在内部量增量。
 *
 * ## 精度与缺锚
 *
 * 锚点之间最多漂移一个 ping 周期的晶振误差（默认 60 秒，几十 ppm 即毫秒量级），再加
 * 服务端到本机的单向延迟（同一网络下几十毫秒），合计远小于本系统的秒级刻度。
 * [nowRfc3339] 在尚无锚点时返回 null——调用方据此不携带该字段，由服务端退回本机接收
 * 时刻。设备刚启动、还没握上手时是这种状态，通常只持续到第一帧 `link.hello_ack`。
 */
object ServerClock {

    /** 一次锚定的成对读数：锚定的服务端时刻（Unix 毫秒）与那一刻的本机单调钟读数。不可变。 */
    private data class Anchor(val serverMs: Long, val uptimeMs: Long)

    /** 当前锚点；null 表示尚未握上手。整体交换保证并发下的成对自洽（见类注释）。 */
    @Volatile
    private var current: Anchor? = null

    /**
     * 用服务端下发的时刻重新锚定。握手应答与每一轮 ping 都调，锚点因此始终新鲜。
     * 解析不出格式就保持原锚点不动：宁可用一个稍旧的锚点，也不能让坏值把时间轴带偏。
     */
    fun anchor(serverTimeRfc3339: String) {
        val moment = Clock.parseRfc3339(serverTimeRfc3339) ?: return
        current = Anchor(serverMs = moment.time, uptimeMs = Uptime.nowMs())
    }

    /** 当前的服务端时刻；尚无锚点时返回 null。 */
    fun nowRfc3339(): String? {
        val at = current ?: return null
        return Clock.formatRfc3339(Date(at.serverMs + (Uptime.nowMs() - at.uptimeMs)))
    }

    /**
     * 清空锚点（测试隔离用）。锚点是单例状态，会在同一 JVM 的用例间泄漏——需要
     * 断言"无锚点"形态的用例先调它。
     */
    internal fun reset() {
        current = null
    }
}
