package com.ryanliu.ntushower.core.logfile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * 有上限的日志文件。
 *
 * 关键是超限时**丢掉最旧的一半**而不是清空整个文件：崩溃循环里最该留下的是最开始那几条，
 * 它们说明是什么把设备引到那一步；清空之后只剩最后一条，反而看不出因果。
 */
class CappedLogFileTest {

    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun `appends while under the cap`() {
        val file = folder.newFile("log.txt")

        CappedLogFile.append(file, "one\n", CAP)
        CappedLogFile.append(file, "two\n", CAP)

        assertEquals("one\ntwo\n", file.readText())
    }

    @Test
    fun `drops the oldest half once the cap is crossed`() {
        val file = folder.newFile("log.txt")
        val lines = (1..7).map { "%03d".format(it) + "x".repeat(96) + "\n" } // 每行 100 字节

        lines.forEach { CappedLogFile.append(file, it, CAP) }

        val text = file.readText()
        assertFalse("最旧的那几行应当已被裁掉", text.startsWith(lines[0]))
        assertTrue("最近的记录必须还在", text.endsWith(lines[6]))
        assertFalse("不该只剩最后一行——那样与清空无异", text == lines[6])
    }

    @Test
    fun `trim never leaves half a multi-byte character`() {
        val file = folder.newFile("log.txt")
        val row = "汉".repeat(30) // 90 字节；按字节硬切会把它劈成两半
        repeat(20) { CappedLogFile.append(file, "$row\n", 200) }

        val rows = file.readText().trimEnd('\n').split('\n')
        assertTrue("应当留下多行", rows.size > 1)
        rows.forEach { assertEquals("每一行都应当是完整的 30 个汉字", row, it) }
    }

    @Test
    fun `unwritable target does not throw`() {
        // 路径不可写（设备只读、单测里没有 /sdcard）时调用方不该受影响
        CappedLogFile.append(java.io.File("/nonexistent-dir/log.txt"), "boom\n", CAP)
    }

    @Test
    fun `a file under the keep size is never trimmed`() {
        // 恰好压线（文件 <= 上限一半）时不裁剪：trim 的守门分支，与"超限裁一半"
        // 的既有用例合起来盖住裁剪判定的两侧
        val file = folder.newFile("small.txt")
        val newline = "\n"
        val content = (1..2).joinToString("") { "%03d".format(it) + "x".repeat(96) + newline } // 200 字节 = CAP 的一半

        content.forEach { CappedLogFile.append(file, it.toString(), CAP) }

        assertEquals(content, file.readText())
    }

    private companion object {
        const val CAP = 500L
    }
}
