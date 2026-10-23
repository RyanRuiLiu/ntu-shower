package com.ryanliu.ntushower.features.voice

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceClipSpecTest {

    private val clipId = "a".repeat(64)
    private val sha256 = "b".repeat(64)

    private fun params(volume: Int? = 3): JSONObject {
        val json = JSONObject()
            .put("clip_id", clipId)
            .put("sha256", sha256)
            .put("size_bytes", 100L)
            .put("duration_ms", 1_000L)
        if (volume != null) json.put("volume", volume)
        return json
    }

    @Test
    fun `play parses all fields`() {
        val spec = VoiceClipSpec.parsePlay(params())!!

        assertEquals(clipId, spec.clipId)
        assertEquals(sha256, spec.sha256)
        assertEquals(100L, spec.sizeBytes)
        assertEquals(1_000L, spec.durationMs)
        assertEquals(3, spec.volume)
    }

    @Test
    fun `prepare does not require volume`() {
        val spec = VoiceClipSpec.parsePrepare(params(volume = null))!!

        assertEquals(0, spec.volume)
    }

    @Test
    fun `play requires volume`() {
        assertNull(VoiceClipSpec.parsePlay(params(volume = null)))
    }

    @Test
    fun `rejects invalid identity and ranges`() {
        assertNull(VoiceClipSpec.parsePlay(params().put("clip_id", "abc")))
        assertNull(VoiceClipSpec.parsePlay(params().put("sha256", "abc")))
        assertNull(VoiceClipSpec.parsePlay(params().put("size_bytes", 0L)))
        assertNull(VoiceClipSpec.parsePlay(params().put("size_bytes", 5L * 1024 * 1024 + 1)))
        assertNull(VoiceClipSpec.parsePlay(params().put("duration_ms", 0L)))
        assertNull(VoiceClipSpec.parsePlay(params().put("duration_ms", 30_001L)))
        assertNull(VoiceClipSpec.parsePlay(params(volume = -1)))
        assertNull(VoiceClipSpec.parsePlay(params(volume = 6)))
    }

    @Test
    fun `data class behavior`() {
        val spec = VoiceClipSpec.parsePlay(params())!!
        val copy = spec.copy()

        assertEquals(spec, copy)
        assertEquals(spec.hashCode(), copy.hashCode())
        assertTrue(spec.toString().contains(clipId))
    }
}
