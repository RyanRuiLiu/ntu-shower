package com.ryanliu.ntushower.core.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ShellOutputTest {

    private fun lines(vararg values: String): Sequence<String> = values.asSequence()

    @Test
    fun `collects lines before marker`() {
        assertEquals("line1\nline2", ShellOutput.collect(lines("line1", "line2", "MARK"), "MARK"))
    }

    @Test
    fun `returns null when marker never appears`() {
        // 命令仍在执行：返回部分结果会让调用方误以为已完成
        assertNull(ShellOutput.collect(lines("line1", "line2"), "MARK"))
    }

    @Test
    fun `returns empty string for immediate marker`() {
        assertEquals("", ShellOutput.collect(lines("MARK", "unread"), "MARK"))
    }

    @Test
    fun `only matches the exact marker line`() {
        // 命令自身输出里可能包含与标记相似的文本
        assertEquals("MARK-ish\nprefix MARK", ShellOutput.collect(lines("MARK-ish", "prefix MARK", "MARK"), "MARK"))
    }

    @Test
    fun `stops at first marker and leaves the rest unread`() {
        val source = lines("a", "MARK", "b").iterator()

        assertEquals("a", ShellOutput.collect(source.asSequence(), "MARK"))
        // 标记之后的内容属于下一条命令，不得被本次读取吞掉
        assertEquals("b", source.next())
    }

    @Test
    fun `preserves blank lines inside output`() {
        assertEquals("a\n\nc", ShellOutput.collect(lines("a", "", "c", "MARK"), "MARK"))
    }

    @Test
    fun `stops accumulating at the byte limit and marks the result`() {
        // 上限是必备的：一条 dumpsys 的输出整块读进内存，足以让 agent 被系统杀掉
        val output = ShellOutput.collect(lines("aaaa", "bbbb", "cccc", "MARK"), "MARK", maxBytes = 6)

        assertEquals("aaaa" + ShellOutput.TRUNCATION_NOTE, output)
    }

    @Test
    fun `keeps reading to the marker after hitting the limit`() {
        // 这条流归后续命令共用：超限就提前退出，会把残余输出留给下一条命令读
        val source = lines("aaaa", "bbbb", "cccc", "MARK", "next").iterator()

        ShellOutput.collect(source.asSequence(), "MARK", maxBytes = 2)

        assertEquals("next", source.next())
    }

    @Test
    fun `does not mark output that fits the limit`() {
        assertEquals("a\nb", ShellOutput.collect(lines("a", "b", "MARK"), "MARK", maxBytes = 1024))
    }
}
