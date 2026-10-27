package com.ryanliu.ntushower.features.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceClipCachePolicyTest {

    private fun file(name: String, size: Long, modified: Long) = VoiceClipFile(name, size, modified)

    @Test
    fun `under limit deletes nothing`() {
        val files = listOf(file("a.mp3", 40, 1), file("b.mp3", 40, 2))

        assertTrue(VoiceClipCachePolicy.filesToDelete(files, 100, setOf("b.mp3")).isEmpty())
    }

    @Test
    fun `over limit deletes the oldest`() {
        val files = listOf(file("old.mp3", 60, 1), file("new.mp3", 60, 2))

        assertEquals(listOf("old.mp3"), VoiceClipCachePolicy.filesToDelete(files, 70, setOf("new.mp3")))
    }

    @Test
    fun `keeps the current file even when it is the oldest`() {
        val files = listOf(file("current.mp3", 60, 1), file("other.mp3", 60, 2))

        assertEquals(listOf("other.mp3"), VoiceClipCachePolicy.filesToDelete(files, 70, setOf("current.mp3")))
    }

    @Test
    fun `keeps every file in the current playlist`() {
        val files = listOf(file("a.mp3", 60, 1), file("b.mp3", 60, 2), file("other.mp3", 60, 3))

        assertEquals(
            listOf("other.mp3"),
            VoiceClipCachePolicy.filesToDelete(files, 130, setOf("a.mp3", "b.mp3")),
        )
    }

    @Test
    fun `deletes everything except the current file when necessary`() {
        val files = listOf(
            file("current.mp3", 80, 3),
            file("old.mp3", 80, 1),
            file("middle.mp3", 80, 2),
        )

        assertEquals(
            listOf("old.mp3", "middle.mp3"),
            VoiceClipCachePolicy.filesToDelete(files, 10, setOf("current.mp3")),
        )
    }
}
