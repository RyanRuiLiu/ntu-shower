package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SerialCommandQueueTest {

    private fun run(result: SerialCommandQueue.EnqueueResult<String>): String =
        (result as SerialCommandQueue.EnqueueResult.Run).item

    @Test
    fun `first enqueue runs immediately`() {
        val queue = SerialCommandQueue<String>()

        assertEquals("a", run(queue.enqueue("a")))
        assertTrue(queue.isRunning())
    }

    @Test
    fun `second enqueue waits while one is running`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")

        // 浴室 App 的 UI 自动化必须串行：并发会让节点定位互相错位
        assertTrue(queue.enqueue("b") is SerialCommandQueue.EnqueueResult.Queued)
        assertEquals(1, queue.pendingCount())
    }

    @Test
    fun `complete releases the next waiting item`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")
        queue.enqueue("b")

        assertEquals("b", queue.complete())
        assertTrue(queue.isRunning())
        assertEquals(0, queue.pendingCount())
    }

    @Test
    fun `complete on empty queue leaves it idle`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")

        assertNull(queue.complete())
        assertFalse(queue.isRunning())
    }

    @Test
    fun `preserves FIFO order`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")
        queue.enqueue("b")
        queue.enqueue("c")

        assertEquals("b", queue.complete())
        assertEquals("c", queue.complete())
        assertNull(queue.complete())
    }

    @Test
    fun `enqueue after idle runs immediately again`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")
        queue.complete()

        assertEquals("b", run(queue.enqueue("b")))
    }

    @Test
    fun `does not drop items enqueued while running`() {
        val queue = SerialCommandQueue<Int>(maxPending = 8)
        queue.enqueue(1)
        repeat(5) { queue.enqueue(it + 2) }

        assertEquals(5, queue.pendingCount())
        assertEquals(2, queue.complete())
        assertEquals(3, queue.complete())
    }

    @Test
    fun `rejects beyond the pending limit`() {
        // 上限挡住的是「服务端多次超时之后」的堆积：它每次超时都放行下一条，
        // 而设备这边可能还在跑上一条。堆着的迟早也会超时，不如当场拒掉。
        val queue = SerialCommandQueue<String>(maxPending = 2)
        queue.enqueue("running")
        queue.enqueue("waiting-1")
        queue.enqueue("waiting-2")

        assertTrue(queue.enqueue("overflow") is SerialCommandQueue.EnqueueResult.Rejected)
        assertEquals(2, queue.pendingCount())
    }

    @Test
    fun `a rejected item does not consume a slot`() {
        val queue = SerialCommandQueue<String>(maxPending = 1)
        queue.enqueue("running")
        queue.enqueue("waiting")
        queue.enqueue("overflow")

        // 被拒的那条不该占位：complete 放行的仍然是先前排队的那一条
        assertEquals("waiting", queue.complete())
    }

    @Test
    fun `completing frees room for a new item`() {
        val queue = SerialCommandQueue<String>(maxPending = 1)
        queue.enqueue("running")
        queue.enqueue("waiting")
        queue.complete() // waiting 开始跑，等待位空出来

        assertTrue(queue.enqueue("accepted") is SerialCommandQueue.EnqueueResult.Queued)
    }

    @Test
    fun `default limit matches the server channel cap`() {
        // 与服务端 CHANNEL_QUEUE_MAX 取同一个数：两边看法一致，排障时不必换算
        assertEquals(4, SerialCommandQueue.DEFAULT_MAX_PENDING)
    }
}
