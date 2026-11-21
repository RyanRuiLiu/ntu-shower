package com.ryanliu.ntushower.features.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoicePcmAssemblerTest {

    @Test
    fun `trims leading silence and fades in`() {
        val silence = IntArray(500) { 0 }
        val voice = IntArray(300) { 1_000 }
        val input = pcm(*silence, *voice)
        val result = VoicePcmAssembler.trimAndFade(input, 44_100, 1)

        assertTrue(result.size in (voice.size * 2 + 1) until input.size)
        assertEquals(0, sampleAt(result, 0))
        assertEquals(1_000, sampleAt(result, result.size / 2 - 1))
        assertTrue(sampleAt(result, 451) in 1..500)
    }

    @Test
    fun `all silence stays untouched`() {
        val bytes = pcm(*IntArray(10) { 0 })
        assertEquals(bytes.size, VoicePcmAssembler.trimAndFade(bytes, 44_100, 1).size)
    }

    @Test
    fun `silence and duration are sample accurate`() {
        assertEquals(44_100, VoicePcmAssembler.silenceBytes(500, 44_100, 1))
        assertEquals(1_000L, VoicePcmAssembler.durationMs(ByteArray(88_200), 44_100, 1))
    }

    @Test
    fun `pause before any clip sets the stream format`() {
        // 清单以停顿开头：格式在第一段 Clip 之前就要定下来，用约定采样率合成静音，
        // 后续 Clip 必须与之一致——先静音后音频的清单是合法形态
        val stream = VoicePcmAssembler.assemble(
            listOf(VoicePcmAssembler.Part.Pause(50), VoicePcmAssembler.Part.Clip(pcm(1_000, 1_000), 44_100, 1)),
        )

        assertNotNull(stream)
        requireNotNull(stream)
        assertEquals(44_100, stream.sampleRate)
        assertEquals(4_410, VoicePcmAssembler.silenceBytes(50, 44_100, 1))
        assertTrue(stream.bytes.size > 4_410)
    }

    @Test
    fun `assembles clips and pause into one stream`() {
        val clip = pcm(0, 0, 1_000, 1_000, 1_000, 1_000)
        val stream = VoicePcmAssembler.assemble(
            listOf(
                VoicePcmAssembler.Part.Clip(clip, 44_100, 1),
                VoicePcmAssembler.Part.Pause(100),
                VoicePcmAssembler.Part.Clip(clip, 44_100, 1),
            ),
        )

        assertNotNull(stream)
        requireNotNull(stream)
        assertEquals(44_100, stream.sampleRate)
        assertEquals(1, stream.channels)
        assertEquals(8_820, VoicePcmAssembler.silenceBytes(100, 44_100, 1))
        assertTrue(stream.bytes.size > 8_820)
        assertEquals(
            VoicePcmAssembler.durationMs(stream.bytes, stream.sampleRate, stream.channels),
            stream.durationMs,
        )
    }

    @Test
    fun `rejects empty or mismatched formats`() {
        assertNull(VoicePcmAssembler.assemble(emptyList()))
        assertNull(
            VoicePcmAssembler.assemble(
                listOf(VoicePcmAssembler.Part.Clip(pcm(1_000, 1_000), 22_050, 1)),
            ),
        )
        assertNull(
            VoicePcmAssembler.assemble(
                listOf(VoicePcmAssembler.Part.Clip(pcm(1_000, 1_000), 44_100, 2)),
            ),
        )
    }

    private fun pcm(vararg samples: Int): ByteArray {
        val bytes = ByteArray(samples.size * 2)
        samples.forEachIndexed { index, value ->
            bytes[index * 2] = (value and 0xFF).toByte()
            bytes[index * 2 + 1] = ((value shr 8) and 0xFF).toByte()
        }
        return bytes
    }

    private fun sampleAt(bytes: ByteArray, index: Int): Int =
        (bytes[index * 2].toInt() and 0xFF) or (bytes[index * 2 + 1].toInt() shl 8)
}
