package com.ryanliu.ntushower.features.automation

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SerialCommandQueueTest {

    @Test
    fun `first enqueue runs immediately`() {
        val queue = SerialCommandQueue<String>()

        assertEquals("a", queue.enqueue("a"))
        assertTrue(queue.isRunning())
    }

    @Test
    fun `second enqueue waits while one is running`() {
        val queue = SerialCommandQueue<String>()
        queue.enqueue("a")

        // 浴室 App 的 UI 自动化必须串行：并发会让节点定位互相错位
        assertNull(queue.enqueue("b"))
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

        assertEquals("b", queue.enqueue("b"))
    }

    @Test
    fun `does not drop items enqueued while running`() {
        val queue = SerialCommandQueue<Int>()
        queue.enqueue(1)
        repeat(5) { queue.enqueue(it + 2) }

        assertEquals(5, queue.pendingCount())
        assertEquals(2, queue.complete())
        assertEquals(3, queue.complete())
    }
}
