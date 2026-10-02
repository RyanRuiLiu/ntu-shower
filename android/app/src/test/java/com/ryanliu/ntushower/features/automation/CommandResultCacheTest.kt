package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 幂等接收者：重复投递的处理。
 *
 * 这一条不测会让 `session.assign` / `session.settle` 在重连重发时重复操作硬件——
 * 那是真实世界的副作用，不是可忽略的重复计算。
 */
class CommandResultCacheTest {

    private fun outcome(id: String, status: String = "ok") = CommandOutcome(id, status, "", "")

    @Test
    fun `returns null for an unseen command`() {
        val cache = CommandResultCache(capacity = 4)

        assertNull(cache.recall("cmd-1"))
    }

    @Test
    fun `remembers and recalls a result`() {
        val cache = CommandResultCache(capacity = 4)
        cache.remember(outcome("cmd-1"))

        assertEquals("ok", cache.recall("cmd-1")?.status)
    }

    @Test
    fun `recalls the latest result for the same command id`() {
        val cache = CommandResultCache(capacity = 4)
        cache.remember(outcome("cmd-1", "ok"))
        cache.remember(outcome("cmd-1", "failed"))

        assertEquals("failed", cache.recall("cmd-1")?.status)
        assertEquals(1, cache.size())
    }

    @Test
    fun `evicts the oldest entry beyond capacity`() {
        val cache = CommandResultCache(capacity = 2)
        cache.remember(outcome("cmd-1"))
        cache.remember(outcome("cmd-2"))
        cache.remember(outcome("cmd-3"))

        assertEquals(2, cache.size())
        assertNull("最久未用的应被淘汰", cache.recall("cmd-1"))
        assertEquals("ok", cache.recall("cmd-3")?.status)
    }

    @Test
    fun `does not grow without bound`() {
        // 设备长期运行：缓存必须是有界的，否则内存随命令数增长
        val cache = CommandResultCache(capacity = 8)
        repeat(1000) { cache.remember(outcome("cmd-$it")) }

        assertEquals(8, cache.size())
    }

    @Test
    fun `ignores outcomes without a command id`() {
        // 空 command_id 无法作为幂等键，记进去只会占用容量
        val cache = CommandResultCache(capacity = 4)
        cache.remember(outcome(""))

        assertEquals(0, cache.size())
    }

    @Test
    fun `shrinking the capacity trims immediately`() {
        // 上限是配置项、可以在运行中变小；removeEldestEntry 只在写入时被调用，
        // 不主动裁的话"窗口已经调小"要等下一次写入才成立
        val cache = CommandResultCache(capacity = 8)
        repeat(8) { cache.remember(outcome("cmd-$it")) }

        cache.setCapacity(3)

        assertEquals(3, cache.size())
        assertNull("超出新窗口的应当已被淘汰", cache.recall("cmd-0"))
        assertEquals("最近的必须留着", "ok", cache.recall("cmd-7")?.status)
    }

    @Test
    fun `growing the capacity keeps existing entries`() {
        val cache = CommandResultCache(capacity = 2)
        cache.remember(outcome("cmd-1"))
        cache.remember(outcome("cmd-2"))

        cache.setCapacity(16)

        assertEquals(2, cache.size())
        assertEquals("ok", cache.recall("cmd-1")?.status)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `rejects non-positive capacity`() {
        CommandResultCache(capacity = 0)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `setCapacity rejects non-positive values`() {
        // 0 会让每次写入都清空去重表，等于关掉幂等
        CommandResultCache(capacity = 4).setCapacity(0)
    }
}
