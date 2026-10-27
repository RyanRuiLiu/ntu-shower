package com.ryanliu.ntushower.features.voice

import java.io.ByteArrayOutputStream
import kotlin.math.abs
import kotlin.math.min

/**
 * 纯 PCM 拼流：解码后的片段与静音段按顺序拼成一条 44.1k 单声道流。
 *
 * 这里不碰 MediaCodec / AudioTrack，便于在 JVM 单测里验证开头的编码器延迟、
 * 淡入、停顿长度和格式一致性；解码外壳在 VoicePcmDecoder 里。
 */
object VoicePcmAssembler {

    const val SAMPLE_RATE = 44_100
    const val CHANNELS = 1
    const val BYTES_PER_SAMPLE = 2

    private const val SILENCE_THRESHOLD = 300
    private const val FADE_MS = 5
    private const val PRE_ROLL_MS = 10
    private const val MS_PER_SECOND = 1000

    data class Stream(val bytes: ByteArray, val sampleRate: Int, val channels: Int, val durationMs: Long)

    sealed class Part {
        class Clip(val bytes: ByteArray, val sampleRate: Int, val channels: Int) : Part()

        class Pause(val ms: Long) : Part()
    }

    /** 任一片段格式不一致或为空时返回 null，由播放器按播放失败处理。 */
    fun assemble(parts: List<Part>): Stream? {
        if (parts.isEmpty()) return null
        val output = ByteArrayOutputStream()
        var sampleRate = 0
        var channels = 0
        for (part in parts) {
            when (part) {
                is Part.Clip -> {
                    if (part.sampleRate != SAMPLE_RATE || part.channels != CHANNELS) return null
                    if (sampleRate == 0) {
                        sampleRate = part.sampleRate
                        channels = part.channels
                    }
                    output.write(trimAndFade(part.bytes, sampleRate, channels))
                }

                is Part.Pause -> {
                    if (sampleRate == 0) {
                        sampleRate = SAMPLE_RATE
                        channels = CHANNELS
                    }
                    output.write(ByteArray(silenceBytes(part.ms, sampleRate, channels)))
                }
            }
        }
        if (output.size() == 0) return null
        val bytes = output.toByteArray()
        return Stream(bytes, sampleRate, channels, durationMs(bytes, sampleRate, channels))
    }

    /** 去掉开头静音（同时去掉 MP3 编码器延迟），并在第一个有效样本处做淡入。 */
    fun trimAndFade(bytes: ByteArray, sampleRate: Int, channels: Int): ByteArray {
        val frameBytes = BYTES_PER_SAMPLE * channels
        var start = 0
        while (start + BYTES_PER_SAMPLE - 1 < bytes.size) {
            if (abs(sampleAt(bytes, start)) > SILENCE_THRESHOLD) break
            start += frameBytes
        }
        if (start >= bytes.size) return bytes
        // 保留起音前 10ms：软辅音可能低于能量阈值，不能被当成静音切掉
        val speechFrame = start / frameBytes
        start = maxOf(0, start - sampleRate * PRE_ROLL_MS / MS_PER_SECOND * frameBytes)
        val result = bytes.copyOfRange(start, bytes.size)
        val totalFrames = result.size / frameBytes
        val fadeStart = min((speechFrame - start / frameBytes).coerceAtLeast(0), totalFrames)
        val fadeFrames = min(sampleRate * FADE_MS / MS_PER_SECOND, totalFrames - fadeStart)
        for (frame in 0 until fadeFrames) {
            val gain = frame.toDouble() / fadeFrames
            val target = fadeStart + frame
            for (channel in 0 until channels) {
                val offset = target * frameBytes + channel * BYTES_PER_SAMPLE
                putSample(result, offset, (sampleAt(result, offset) * gain).toInt())
            }
        }
        return result
    }

    fun silenceBytes(ms: Long, sampleRate: Int, channels: Int): Int =
        (ms * sampleRate * BYTES_PER_SAMPLE * channels / MS_PER_SECOND).toInt()

    fun durationMs(bytes: ByteArray, sampleRate: Int, channels: Int): Long =
        bytes.size.toLong() * MS_PER_SECOND / (BYTES_PER_SAMPLE * channels * sampleRate)

    private fun sampleAt(bytes: ByteArray, offset: Int): Int =
        (bytes[offset].toInt() and 0xFF) or (bytes[offset + 1].toInt() shl 8)

    private fun putSample(bytes: ByteArray, offset: Int, value: Int) {
        val clamped = value.coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
        bytes[offset] = (clamped and 0xFF).toByte()
        bytes[offset + 1] = ((clamped shr 8) and 0xFF).toByte()
    }
}
