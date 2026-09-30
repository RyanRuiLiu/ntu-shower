package com.ryanliu.ntushower.core.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ShellOutputTest {

    @Test
    fun `collects lines before marker`() {
        val lines = listOf("line1", "line2", "MARK").iterator()

        assertEquals("line1\nline2", ShellOutput.collect(lines, "MARK"))
    }

    @Test
    fun `returns null when marker never appears`() {
        // 命令仍在执行：返回部分结果会让调用方误以为已完成
        val lines = listOf("line1", "line2").iterator()

        assertNull(ShellOutput.collect(lines, "MARK"))
    }

    @Test
    fun `returns empty string for immediate marker`() {
        val lines = listOf("MARK", "unread").iterator()

        assertEquals("", ShellOutput.collect(lines, "MARK"))
    }

    @Test
    fun `only matches the exact marker line`() {
        // 命令自身输出里可能包含与标记相似的文本
        val lines = listOf("MARK-ish", "prefix MARK", "MARK").iterator()

        assertEquals("MARK-ish\nprefix MARK", ShellOutput.collect(lines, "MARK"))
    }

    @Test
    fun `stops at first marker and leaves the rest unread`() {
        val source = listOf("a", "MARK", "b").iterator()

        assertEquals("a", ShellOutput.collect(source, "MARK"))
        // 标记之后的内容属于下一条命令，不得被本次读取吞掉
        assertEquals("b", source.next())
    }

    @Test
    fun `preserves blank lines inside output`() {
        val lines = listOf("a", "", "c", "MARK").iterator()

        assertEquals("a\n\nc", ShellOutput.collect(lines, "MARK"))
    }
}
