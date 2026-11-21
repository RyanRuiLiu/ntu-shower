package com.ryanliu.ntushower.features.automation

import java.util.concurrent.ConcurrentLinkedQueue

/**
 * 串行放行队列（纯逻辑，可单测）。
 *
 * 语义：**同一时刻只放行一条**。入队时若空闲则立即返回该条供调用方执行；
 * 执行完调用 [complete] 取走下一条。浴室 App 的 UI 自动化必须串行，
 * 并发操作会让节点定位互相错位。
 *
 * 与线程池解耦：本类只决定"谁可以执行"，不负责执行。
 *
 * ## 为什么还要有上限
 *
 * 服务端那边也给每条通道设了在途上限，但它**超时会释放锁**——放弃等待、记为超时、
 * 立刻发下一条。设备并不知道对面已经不等了，可能还在跑第一条。所以**每次超时都会
 * 让设备端多排一条**，次数不封顶。
 *
 * 截图这类慢操作（1-3 秒）叠上几次超时就够把队列堆高。超过 [maxPending] 时
 * [enqueue] 返回 [EnqueueResult.Rejected]，由调用方回报"服务端过载"——真实原因
 * 是设备这一侧忙不过来，而对调用方来说两者是同一种处境：现在别发，等会儿再试。
 */
class SerialCommandQueue<T>(private val maxPending: Int = DEFAULT_MAX_PENDING) {

    private val queue = ConcurrentLinkedQueue<T>()

    @Volatile
    private var running = false

    /** 入队结果。区分"排满了"与"正常排队"，前者要回报给调用方。 */
    sealed interface EnqueueResult<out T> {

        /** 应立刻执行。 */
        data class Run<T>(val item: T) : EnqueueResult<T>

        /** 已排队，等前面的跑完。 */
        data object Queued : EnqueueResult<Nothing>

        /** 排满了，拒绝。 */
        data object Rejected : EnqueueResult<Nothing>
    }

    /**
     * 入队。
     *
     * 空闲时返回 [EnqueueResult.Run]（调用方立刻执行）；否则排队或拒绝。
     */
    @Synchronized
    fun enqueue(item: T): EnqueueResult<T> {
        if (!running) {
            running = true
            return EnqueueResult.Run(item)
        }
        if (queue.size >= maxPending) return EnqueueResult.Rejected
        queue.add(item)
        return EnqueueResult.Queued
    }

    /**
     * 标记当前这条已完成，并取走下一条。
     *
     * @return 下一条待执行元素；队列已空时返回 null
     */
    @Synchronized
    fun complete(): T? {
        running = false
        val next = queue.poll() ?: return null
        running = true
        return next
    }

    /** 等待中的条数（不含正在执行的那条）。 */
    fun pendingCount(): Int = queue.size

    /** 当前是否有命令在执行。 */
    fun isRunning(): Boolean = running

    companion object {
        /**
         * 等待上限。
         *
         * 与服务端 `CHANNEL_QUEUE_MAX`（modules/devices/actions.py）同值：两边看法
         * 一致，排障时不必换算。
         */
        // 同通道排队上限两端同值。语音风暴时一扇门一条播报，喷头最多的设备同时
        // 迁移就是 8 场，低于它后排播报会被当成过载拒掉
        const val DEFAULT_MAX_PENDING = 8
    }
}
