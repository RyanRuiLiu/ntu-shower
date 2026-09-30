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
 */
class SerialCommandQueue<T> {

    private val queue = ConcurrentLinkedQueue<T>()

    @Volatile
    private var running = false

    /**
     * 入队。
     *
     * @return 空闲时返回应立刻执行的元素；已有命令在执行时返回 null（等 [complete] 放行）
     */
    @Synchronized
    fun enqueue(item: T): T? {
        if (running) {
            queue.add(item)
            return null
        }
        running = true
        return item
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
}
