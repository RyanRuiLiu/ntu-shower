package com.ryanliu.ntushower.features.voice

import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import com.ryanliu.ntushower.core.log.DeviceLog
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * MP3 片段解码层：把每个片段解成 PCM，拼流交给 [VoicePcmAssembler]。
 * MediaCodec/MediaExtractor 是平台绑定层，不参与 JVM 覆盖率；纯拼流逻辑在 assembler。
 */
class VoicePcmDecoder(private val deviceLog: DeviceLog) {

    private data class Decoded(val sampleRate: Int, val channels: Int, val bytes: ByteArray)

    fun assemble(segments: List<VoiceSegment>, files: Map<String, File>): VoicePcmAssembler.Stream? {
        val cache = HashMap<String, VoicePcmAssembler.Part.Clip>()
        val parts = ArrayList<VoicePcmAssembler.Part>(segments.size)
        for (segment in segments) {
            when (segment) {
                is VoiceSegment.Clip -> {
                    val part = cache.getOrPut(segment.clip.clipId) {
                        val file = files[segment.clip.clipId] ?: return null
                        val decoded = decode(file, segment.clip.clipId) ?: return null
                        VoicePcmAssembler.Part.Clip(decoded.bytes, decoded.sampleRate, decoded.channels)
                    }
                    parts += part
                }

                is VoiceSegment.Pause -> parts += VoicePcmAssembler.Part.Pause(segment.ms)
            }
        }
        return VoicePcmAssembler.assemble(parts)
    }

    @Suppress("DEPRECATION", "TooGenericExceptionCaught", "LongMethod", "CyclomaticComplexMethod", "NestedBlockDepth")
    private fun decode(file: File, clipId: String): Decoded? {
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        try {
            extractor.setDataSource(file.absolutePath)
            val trackIndex = (0 until extractor.trackCount).firstOrNull { index ->
                extractor.getTrackFormat(index).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
            } ?: return null
            extractor.selectTrack(trackIndex)
            val format = extractor.getTrackFormat(trackIndex)
            val mime = format.getString(MediaFormat.KEY_MIME) ?: return null
            val decoder = MediaCodec.createDecoderByType(mime)
            codec = decoder
            decoder.configure(format, null, null, 0)
            decoder.start()

            val output = ByteArrayOutputStream()
            val info = MediaCodec.BufferInfo()
            var inputDone = false
            var outputDone = false
            var sampleRate = format.getInteger(MediaFormat.KEY_SAMPLE_RATE)
            var channels = format.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
            val deadline = System.nanoTime() + DECODE_TIMEOUT_NS
            while (!outputDone) {
                if (System.nanoTime() > deadline) {
                    deviceLog.error("audio.pcm-timeout", "clip=$clipId")
                    return null
                }
                if (!inputDone) {
                    val inputIndex = decoder.dequeueInputBuffer(DEQUEUE_TIMEOUT_US)
                    if (inputIndex >= 0) {
                        val buffer = decoder.inputBuffers[inputIndex]
                        val size = extractor.readSampleData(buffer, 0)
                        if (size < 0) {
                            decoder.queueInputBuffer(inputIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                            inputDone = true
                        } else {
                            decoder.queueInputBuffer(inputIndex, 0, size, extractor.sampleTime, 0)
                            extractor.advance()
                        }
                    }
                }
                when (val outputIndex = decoder.dequeueOutputBuffer(info, DEQUEUE_TIMEOUT_US)) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val changed = decoder.outputFormat
                        sampleRate = changed.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                        channels = changed.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                    }

                    MediaCodec.INFO_TRY_AGAIN_LATER, MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> Unit

                    else -> {
                        if (outputIndex >= 0) {
                            if (info.size > 0) {
                                val buffer = decoder.outputBuffers[outputIndex]
                                val chunk = ByteArray(info.size)
                                buffer.position(info.offset)
                                buffer.limit(info.offset + info.size)
                                buffer.get(chunk)
                                output.write(chunk)
                            }
                            decoder.releaseOutputBuffer(outputIndex, false)
                            if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) outputDone = true
                        }
                    }
                }
            }
            return Decoded(sampleRate, channels, output.toByteArray())
        } catch (e: Exception) {
            deviceLog.error("audio.pcm-failed", "clip=$clipId ${e.message ?: ""}")
            return null
        } finally {
            runCatching { codec?.stop() }
            runCatching { codec?.release() }
            runCatching { extractor.release() }
        }
    }

    private companion object {
        const val DEQUEUE_TIMEOUT_US = 10_000L
        const val DECODE_TIMEOUT_NS = 10_000_000_000L
    }
}
