package com.ryanliu.ntushower.core.shell

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * 输出收割。
 *
 * 这里每一条都对应真机上出过或差点出过的事：标记与退出码同行、输出没有结尾换行时标记会贴
 * 在内容后面、超限后必须继续读到标记（否则残余输出会喂给下一条命令）。
 */
class ShellOutputTest {

    /** 与真机同形：时间戳加一段随机码。命令自己的输出不可能撞上它。 */
    private val mark = "__DONE_1234_abcd__"

    private fun lines(vararg values: String): Sequence<String> = values.asSequence()

    private fun collect(vararg values: String): ShellOutput.Captured? = ShellOutput.collect(lines(*values), mark)

    @Test
    fun `collects lines before the marker`() {
        assertEquals("line1\nline2", collect("line1", "line2", "${mark}0")?.output)
    }

    @Test
    fun `carries the exit code written after the marker`() {
        assertEquals(0, collect("x", "${mark}0")?.exitCode)
        assertEquals(1, collect("x", "${mark}1")?.exitCode)
        assertEquals(137, collect("x", "${mark}137")?.exitCode)
    }

    @Test
    fun `reads the marker glued to output that has no trailing newline`() {
        // 真机上踩过这个：head -c 的输出没有结尾换行，标记直接贴在了内容后面。
        // 按整行匹配的写法会把这条命令的输出和退出码一起丢掉，而调用方读不到退出码
        // 就只能猜它成没成。
        val captured = collect("aaaa${mark}3")
        assertEquals("aaaa", captured?.output)
        assertEquals(3, captured?.exitCode)
    }

    @Test
    fun `returns null when the marker never appears`() {
        // 命令仍在执行：返回部分结果会让调用方误以为已完成
        assertNull(collect("line1", "line2"))
    }

    @Test
    fun `returns empty output for an immediate marker`() {
        assertEquals("", collect("${mark}0", "unread")?.output)
    }

    @Test
    fun `stops at the marker and leaves the rest unread`() {
        val source = lines("a", "${mark}0", "b").iterator()

        assertEquals("a", ShellOutput.collect(source.asSequence(), mark)?.output)
        // 标记之后的内容属于下一条命令，不得被本次读取吞掉
        assertEquals("b", source.next())
    }

    @Test
    fun `preserves blank lines inside output`() {
        assertEquals("a\n\nc", collect("a", "", "c", "${mark}0")?.output)
    }

    @Test
    fun `stops accumulating at the byte limit and marks the result`() {
        // 上限是必备的：一条 dumpsys 的输出整块读进内存，足以让 agent 被系统杀掉
        val captured = ShellOutput.collect(lines("aaaa", "bbbb", "cccc", "${mark}0"), mark, maxBytes = 6)

        assertEquals("aaaa" + ShellOutput.TRUNCATION_NOTE, captured?.output)
    }

    @Test
    fun `still reports the exit code when the output was truncated`() {
        // 退出码贴在标记上，就不存在"输出被截断，连成没成都不知道"这种情况
        val captured = ShellOutput.collect(lines("aaaa", "bbbb", "${mark}9"), mark, maxBytes = 2)

        assertEquals(9, captured?.exitCode)
    }

    @Test
    fun `keeps reading to the marker after hitting the limit`() {
        // 这条流归后续命令共用：超限就提前退出，会把残余输出留给下一条命令读
        val source = lines("aaaa", "bbbb", "cccc", "${mark}0", "next").iterator()

        ShellOutput.collect(source.asSequence(), mark, maxBytes = 2)

        assertEquals("next", source.next())
    }

    @Test
    fun `does not mark output that fits the limit`() {
        assertEquals("a\nb", ShellOutput.collect(lines("a", "b", "${mark}0"), mark, maxBytes = 1024)?.output)
    }
}
