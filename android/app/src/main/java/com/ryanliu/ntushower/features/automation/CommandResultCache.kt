package com.ryanliu.ntushower.features.automation

import java.util.Collections

/**
 * 命令结果缓存：设备侧的幂等接收者（Enterprise Integration Patterns, Idempotent Receiver）。
 *
 * 为什么必须去重：发送方只发一次，接收方仍可能收到多次（重连重发、网络抖动、
 * 服务端超时重试）。`session.assign` / `session.settle` 会真实操作浴室 App 与硬件，
 * 重复执行不是"多做一次无用功"而是**真的再操作一次**，因此必须在执行前拦截。
 *
 * 命中缓存时调用方重发上次结果，而不是重新执行——服务端可能正是因为没收到结果
 * 才重发，静默忽略会让它一直等到超时。
 *
 * 有界：只保留最近若干条，不随运行时长增长。上限是**配置项**（`command_dedup_max_count`，
 * 服务端登记表下发），见 [setCapacity]。
 */
class CommandResultCache(private var capacity: Int) {

    init {
        require(capacity > 0) { "capacity must be positive" }
    }

    // accessOrder=true + removeEldestEntry：按访问顺序淘汰最久未用的一条
    private val outcomes: MutableMap<String, CommandOutcome> = Collections.synchronizedMap(
        object : LinkedHashMap<String, CommandOutcome>(16, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, CommandOutcome>): Boolean =
                size > capacity
        },
    )

    /**
     * 按新的配置值调整窗口。
     *
     * 上限是配置项、可以在运行中变小，而 [LinkedHashMap.removeEldestEntry] 只在写入时被调用
     * ——不主动裁一次的话，"窗口已经调小"要等到下一次写入才成立，中间那段时间里它仍是旧值。
     */
    fun setCapacity(value: Int) {
        require(value > 0) { "capacity must be positive" }
        capacity = value
        synchronized(outcomes) {
            while (outcomes.size > capacity) {
                val eldest = outcomes.keys.firstOrNull()
                if (eldest == null) break
                outcomes.remove(eldest)
            }
        }
    }

    /** 取该命令上次的结果；从未处理过则返回 null。 */
    fun recall(commandId: String): CommandOutcome? = outcomes[commandId]

    /** 记录一条命令的结果。 */
    fun remember(outcome: CommandOutcome) {
        if (outcome.commandId.isEmpty()) return
        outcomes[outcome.commandId] = outcome
    }

    /** 当前缓存的条数（测试用）。 */
    fun size(): Int = outcomes.size
}
