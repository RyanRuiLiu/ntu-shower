package com.ryanliu.ntushower.features.voice

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceClipSpecTest {

    private val clipId = "a".repeat(64)
    private val sha256 = "b".repeat(64)

    private fun clip(): JSONObject = JSONObject()
        .put("clip_id", clipId)
        .put("sha256", sha256)
        .put("size_bytes", 100L)
        .put("duration_ms", 1_000L)

    private fun clipSegment(): JSONObject = JSONObject()
        .put("kind", "clip")
        .put("clip_id", clipId)
        .put("sha256", sha256)
        .put("size_bytes", 100L)
        .put("duration_ms", 1_000L)

    private fun playParams(volume: Int? = 3): JSONObject {
        val segments = JSONArray()
            .put(clipSegment())
            .put(JSONObject().put("kind", "pause").put("ms", 500L))
            .put(clipSegment())
        val json = JSONObject().put("segments", segments)
        if (volume != null) json.put("volume", volume)
        json.put("download_budget_ms", 60_000L)
        return json
    }

    @Test
    fun `play parses ordered segments`() {
        val spec = VoicePlaySpec.parse(playParams())!!

        assertEquals(3, spec.segments.size)
        assertEquals(3, spec.volume)
        assertEquals(60_000L, spec.downloadBudgetMs)
        assertEquals(1_000L, (spec.segments[0] as VoiceSegment.Clip).clip.durationMs)
        assertEquals(500L, (spec.segments[1] as VoiceSegment.Pause).ms)
    }

    @Test
    fun `prepare parses clip list without volume`() {
        val clips = VoicePlaySpec.parsePrepare(JSONObject().put("clips", JSONArray().put(clip())))!!

        assertEquals(1, clips.size)
        assertEquals(clipId, clips[0].clipId)
    }

    @Test
    fun `play requires volume`() {
        assertNull(VoicePlaySpec.parse(playParams(volume = null)))
        val noBudget = playParams()
        noBudget.remove("download_budget_ms")
        assertNull(VoicePlaySpec.parse(noBudget))
        assertNull(VoicePlaySpec.parse(playParams().put("download_budget_ms", 0L)))
    }

    @Test
    fun `rejects invalid identity and ranges`() {
        val badClip = clipSegment().put("clip_id", "abc")
        val segments = JSONArray().put(badClip)
        assertNull(
            VoicePlaySpec.parse(
                JSONObject().put("segments", segments).put("volume", 3).put("download_budget_ms", 60_000L),
            ),
        )
        assertNull(VoicePlaySpec.parse(playParams(volume = -1)))
        assertNull(VoicePlaySpec.parse(playParams(volume = 6)))
        assertNull(
            VoicePlaySpec.parse(
                JSONObject()
                    .put("segments", JSONArray().put(JSONObject().put("kind", "pause").put("ms", 50L)))
                    .put("volume", 3)
                    .put("download_budget_ms", 60_000L),
            ),
        )
    }

    @Test
    fun `rejects malformed playlists`() {
        assertNull(VoicePlaySpec.parse(JSONObject().put("volume", 3)))
        assertNull(
            VoicePlaySpec.parse(
                JSONObject().put("segments", JSONArray()).put("volume", 3).put("download_budget_ms", 60_000L),
            ),
        )
        assertNull(
            VoicePlaySpec.parse(
                JSONObject()
                    .put("segments", JSONArray().put(JSONObject().put("kind", "bogus")))
                    .put("volume", 3)
                    .put("download_budget_ms", 60_000L),
            ),
        )
        assertNull(
            VoicePlaySpec.parse(
                JSONObject()
                    .put("segments", JSONArray().put(JSONObject().put("kind", "pause").put("ms", 6_000L)))
                    .put("volume", 3)
                    .put("download_budget_ms", 60_000L),
            ),
        )
        val badClip = clipSegment().put("duration_ms", 30_001L)
        assertNull(
            VoicePlaySpec.parse(
                JSONObject()
                    .put("segments", JSONArray().put(badClip))
                    .put("volume", 3)
                    .put("download_budget_ms", 60_000L),
            ),
        )
    }

    @Test
    fun `rejects malformed prepare lists`() {
        assertNull(VoicePlaySpec.parsePrepare(JSONObject()))
        assertNull(VoicePlaySpec.parsePrepare(JSONObject().put("clips", JSONArray())))
        assertNull(
            VoicePlaySpec.parsePrepare(
                JSONObject().put("clips", JSONArray().put(clip().put("size_bytes", 0L))),
            ),
        )
    }

    @Test
    fun `bounds segment and prepare counts`() {
        val segments = JSONArray()
        repeat(32) { segments.put(clipSegment()) }
        val params = JSONObject()
            .put("segments", segments)
            .put("volume", 3)
            .put("download_budget_ms", 60_000L)
        assertTrue(VoicePlaySpec.parse(params) != null)
        segments.put(clipSegment())
        assertNull(VoicePlaySpec.parse(params))

        val clips = JSONArray()
        repeat(42) { clips.put(clip()) }
        assertTrue(VoicePlaySpec.parsePrepare(JSONObject().put("clips", clips)) != null)
        clips.put(clip())
        assertNull(VoicePlaySpec.parsePrepare(JSONObject().put("clips", clips)))
    }

    @Test
    fun `data class behavior`() {
        val spec = VoicePlaySpec.parse(playParams())!!
        val copy = spec.copy()

        assertEquals(spec, copy)
        assertEquals(spec.hashCode(), copy.hashCode())
        assertTrue(spec.toString().contains("Pause"))
    }
}
