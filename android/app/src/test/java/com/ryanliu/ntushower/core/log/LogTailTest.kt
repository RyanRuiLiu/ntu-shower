package com.ryanliu.ntushower.core.log

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class LogTailTest {

    @get:Rule
    val folder = TemporaryFolder()

    @Test
    fun `missing file is reported as absent`() {
        val result = LogTail.read(File(folder.root, "crash.log"), maxLines = 10)

        assertFalse(result.present)
        assertEquals(0, result.lines)
        assertEquals(0L, result.sizeBytes)
        assertFalse(result.truncated)
    }

    @Test
    fun `returns the newest lines and marks truncation`() {
        val file = File(folder.root, "device.log")
        file.writeText((1..5).joinToString("\n") { "line-$it" } + "\n", Charsets.UTF_8)

        val result = LogTail.read(file, maxLines = 2)

        assertTrue(result.present)
        assertEquals("line-4\nline-5", result.content)
        assertEquals(2, result.lines)
        assertTrue(result.truncated)
        assertEquals(file.length(), result.sizeBytes)
    }

    @Test
    fun `byte cap keeps complete lines and utf8 characters`() {
        val file = File(folder.root, "device.log")
        file.writeText((1..20).joinToString("\n") { "第${it}行：中文日志内容" } + "\n", Charsets.UTF_8)

        val result = LogTail.read(file, maxLines = 100, maxBytes = 40)

        assertTrue(result.present)
        assertTrue(result.truncated)
        assertTrue(result.content.isNotBlank())
        assertFalse(result.content.contains('\uFFFD'))
        assertTrue(result.content.startsWith("第"))
    }

    @Test
    fun `empty file is present with no content`() {
        val file = File(folder.root, "device.log")
        file.writeText("")

        val result = LogTail.read(file, maxLines = 10)

        assertTrue(result.present)
        assertEquals("", result.content)
        assertEquals(0, result.lines)
        assertFalse(result.truncated)
    }
}
