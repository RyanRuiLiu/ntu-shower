package com.ryanliu.ntushower.features.remote

import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.util.Base64
import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.shell.RootShell
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer

/**
 * 全屏截图（screenrecord + MediaCodec 取帧）。设备为固定硬件，分辨率是设备属性而非配置项。
 *
 * 为什么必须走这条链路（设备硬件约束，均已真机实测）：
 * - 本机使用硬件合成器（HWC），UI 经 overlay 直出屏幕，framebuffer/screencap 拿不到画面
 *   （实测 `screencap -p` 只产出 86 字节的空 PNG）；
 * - screenrecord 是 4.4+ 内置工具，经 MediaCodec 从 SurfaceFlinger 抓取含 overlay 的画面；
 * - MediaMetadataRetriever.getFrameAtTime() 在老设备上返回缩略图（344x192），
 *   故用 MediaExtractor + MediaCodec 直接解码 H.264 取全分辨率帧；
 * - JPEG q50：实测 q95 体积为其 2.8 倍而 PSNR 36.2dB 不可辨，验收场景够用。
 *
 * 对齐与裁剪：编码器要求宽度 16 像素对齐，1366 被编码为 1376。实测带与不带
 * `--size 1376x768` 输出完全一致（均为 1376x768），故不传 --size。合成器把 1366 宽的
 * 画面**右对齐**放进 1376 宽的编码面——实测两段录屏左侧均为 10 列对齐填充、内容直达
 * 右边缘，因此 JPEG 编码阶段（compressToJpeg 的 Rect）从**右侧锚定**取内容列
 * （`width - 内容宽` 起），裁掉的是左侧对齐列；若从 0 起裁会把黑边留下、把右侧真实
 * 内容（状态栏时钟的末位数字）裁掉。
 */
object ScreenCapture {

    private const val DISPLAY_WIDTH = 1366
    private const val DISPLAY_HEIGHT = 768
    private const val SCREENRECORD_TIME_LIMIT_S = 1 // screenrecord 支持的最小值
    private const val SCREENRECORD_BITRATE = 500_000 // 低码率足够单帧提取
    private const val SCREENSHOT_JPEG_QUALITY = 50
    private const val DECODE_TIMEOUT_MS = 5_000L
    private const val DEQUEUE_TIMEOUT_US = 10_000L

    /** 执行截图并返回 JPEG 的 Base64（NO_WRAP）；失败返回 null。 */
    fun captureJpegBase64(): String? {
        val videoPath = "/sdcard/sc_video_${Uptime.nowMs()}.mp4"
        return try {
            RootShell.control.exec(
                "/system/bin/screenrecord --time-limit $SCREENRECORD_TIME_LIMIT_S " +
                    "--bit-rate $SCREENRECORD_BITRATE " +
                    "$videoPath 2>&1; chmod 666 $videoPath",
            )
            val video = File(videoPath)
            if (!video.exists() || video.length() == 0L) {
                return null
            }
            val jpeg = decodeFirstFrameAsJpeg(videoPath)
            video.delete()
            jpeg?.let { Base64.encodeToString(it, Base64.NO_WRAP) }
        } catch (e: Exception) {
            File(videoPath).delete()
            Log.w(TAG, "screen capture failed: ${e.message}")
            null
        }
    }

    /** MediaExtractor + MediaCodec 解码首帧并直接编码为 JPEG（绕过缩略图路径）。 */
    private fun decodeFirstFrameAsJpeg(videoPath: String): ByteArray? {
        val extractor = MediaExtractor()
        var codec: MediaCodec? = null
        val startedAt = Uptime.nowMs()

        try {
            extractor.setDataSource(videoPath)
            val track = findVideoTrack(extractor) ?: return null
            extractor.selectTrack(track.second)

            val mime = track.first.getString(MediaFormat.KEY_MIME) ?: return null
            codec = MediaCodec.createDecoderByType(mime)
            codec.configure(track.first, null, null, 0)
            codec.start()

            val info = MediaCodec.BufferInfo()
            var inputDone = false
            while (Uptime.nowMs() - startedAt < DECODE_TIMEOUT_MS) {
                if (!inputDone) inputDone = feedInput(extractor, codec, DEQUEUE_TIMEOUT_US)

                val outputIndex = codec.dequeueOutputBuffer(info, DEQUEUE_TIMEOUT_US)
                if (outputIndex >= 0) {
                    val jpeg = if (info.size > 0) encodeFrame(codec, info, outputIndex) else null
                    codec.releaseOutputBuffer(outputIndex, false)
                    if (jpeg != null) return jpeg
                }
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
            }
            return null
        } catch (e: Exception) {
            Log.w(TAG, "decode first frame failed: ${e.message}")
            return null
        } finally {
            runCatching { codec?.stop() }
            runCatching { codec?.release() }
            runCatching { extractor.release() }
        }
    }

    /** 找视频轨道：返回 (format, index)，无则 null。 */
    private fun findVideoTrack(extractor: MediaExtractor): Pair<MediaFormat, Int>? {
        for (i in 0 until extractor.trackCount) {
            val format = extractor.getTrackFormat(i)
            val mime = format.getString(MediaFormat.KEY_MIME)
            if (mime != null && mime.startsWith("video/")) return format to i
        }
        return null
    }

    /**
     * 喂入一个样本到解码器；流结束时发 EOS 并返回 true。
     *
     * 用 inputBuffers 而非 getInputBuffer(index)：后者 API 21 才引入，
     * 而本设备 ROM 冻结在 Android 4.4（API 19），没有替代路径。
     */
    @Suppress("DEPRECATION")
    private fun feedInput(extractor: MediaExtractor, codec: MediaCodec, timeoutUs: Long): Boolean {
        val inputIndex = codec.dequeueInputBuffer(timeoutUs)
        if (inputIndex < 0) return false
        val inputBuffer = codec.inputBuffers[inputIndex]
        inputBuffer.clear()
        val sampleSize = extractor.readSampleData(inputBuffer, 0)
        if (sampleSize < 0) {
            codec.queueInputBuffer(inputIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
            return true
        }
        codec.queueInputBuffer(inputIndex, 0, sampleSize, extractor.sampleTime, 0)
        extractor.advance()
        return false
    }

    /**
     * 取一帧解码输出并编码为 JPEG（含 16 像素对齐区的裁剪）。
     *
     * 同 feedInput：outputBuffers 是 API 19 上唯一可用的取帧途径。
     */
    @Suppress("DEPRECATION")
    private fun encodeFrame(codec: MediaCodec, info: MediaCodec.BufferInfo, outputIndex: Int): ByteArray? {
        val format = codec.outputFormat
        val width = format.getInteger(MediaFormat.KEY_WIDTH)
        val height = format.getInteger(MediaFormat.KEY_HEIGHT)
        val colorFormat = runCatching { format.getInteger(MediaFormat.KEY_COLOR_FORMAT) }
            .getOrDefault(YuvConverter.COLOR_FORMAT_NV12)
        return encodeBuffer(codec.outputBuffers[outputIndex], info.size, width, height, colorFormat)
    }

    /** 解码缓冲 → NV21 → JPEG（编码阶段裁掉对齐区）。 */
    private fun encodeBuffer(
        buffer: ByteBuffer,
        dataSize: Int,
        width: Int,
        height: Int,
        colorFormat: Int,
    ): ByteArray? = runCatching {
        buffer.position(0)
        val yuv = ByteArray(dataSize)
        buffer.get(yuv)

        val nv21 = YuvConverter.toNv21(yuv, width, height, colorFormat)
        val image = YuvImage(nv21, ImageFormat.NV21, width, height, null)
        val output = ByteArrayOutputStream()
        // 对齐列在左侧（见类注释），内容从右锚定；width 已含对齐列时 cropLeft 即对齐列数
        val contentWidth = minOf(width, DISPLAY_WIDTH)
        val contentHeight = minOf(height, DISPLAY_HEIGHT)
        image.compressToJpeg(
            Rect(width - contentWidth, 0, width, contentHeight),
            SCREENSHOT_JPEG_QUALITY,
            output,
        )
        output.toByteArray()
    }.onFailure { Log.w(TAG, "encode JPEG failed: ${it.message}") }.getOrNull()

    private const val TAG = "ScreenCapture"
}
