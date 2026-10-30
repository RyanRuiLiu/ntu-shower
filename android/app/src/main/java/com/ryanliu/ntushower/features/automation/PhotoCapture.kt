// 设备 ROM 冻结在 Android 4.4（API 19）：Camera2 自 API 21 起才有，此处只能用 Camera1；
// 其 API 虽在新版 SDK 中废弃，但在 19 上是唯一可用路径。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.SurfaceTexture
import android.graphics.YuvImage
import android.hardware.Camera
import android.util.Base64
import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicReference

/**
 * 拍照：Camera1 API，预览帧单次捕获 → NV21 → JPEG → Base64。
 *
 * 线程模型：本方法由 CommandScheduler 的拍照执行器调用，该线程**无 Looper**，
 * 因此 Camera 的回调会投递到主线程执行；等待预览帧的轮询循环阻塞的是拍照
 * 执行器线程，不会阻塞主线程。
 */
object PhotoCapture {

    private const val TAG = "PhotoCapture"

    /** 预览目标宽度；实际尺寸取相机支持列表中最接近的一项。 */
    private const val MAX_WIDTH = 1280
    private const val PREVIEW_HEIGHT = 720
    private const val PREVIEW_TIMEOUT_MS = 3_000L
    private const val POLL_INTERVAL_MS = 50L
    private const val JPEG_QUALITY = 60

    /** 返回 JPEG Base64；失败返回 null。 */
    fun takePhoto(): String? = try {
        captureInternal()
    } catch (e: Exception) {
        Log.e(TAG, "photo failed: ${e.message}", e)
        null
    }

    private fun captureInternal(): String? {
        val camera = Camera.open(0)
        val texture = SurfaceTexture(0)
        try {
            texture.setDefaultBufferSize(MAX_WIDTH, PREVIEW_HEIGHT)
            camera.setPreviewTexture(texture)
            val params = camera.parameters
            val size = params.supportedPreviewSizes
                .filter { it.width <= MAX_WIDTH }
                .maxByOrNull { it.width * it.height } ?: params.previewSize
            size?.let { params.setPreviewSize(it.width, it.height) }
            camera.parameters = params

            val captured = AtomicReference<ByteArray?>(null)
            camera.setOneShotPreviewCallback { data, camera1 ->
                captured.set(data)
                camera1.stopPreview()
            }
            camera.startPreview()
            val deadline = Uptime.nowMs() + PREVIEW_TIMEOUT_MS
            while (captured.get() == null && Uptime.nowMs() < deadline) {
                Thread.sleep(POLL_INTERVAL_MS)
            }
            camera.stopPreview()

            val yuv = captured.get() ?: return null
            // 尺寸要回读**相机实际采纳的**，而不是我们请求的：`setPreviewSize` 不保证被接受
            // （相机常把它夹取到最接近的支持尺寸）。拿请求值去解析 YUV，轻则画面错位花屏、
            // 重则 `YuvImage` 直接抛异常——两者都被上层收敛成 `camera.capture_failed`，
            // 现象是"照片随机失败或糊"，而原因藏在一次没人看的参数协商里。
            //
            // 回读也可能为 null（支持列表为空时），退回请求值：有尺寸总比没有强。
            val actual = camera.parameters.previewSize
            val width = actual?.width ?: size?.width ?: MAX_WIDTH
            val height = actual?.height ?: size?.height ?: PREVIEW_HEIGHT
            val out = ByteArrayOutputStream()
            YuvImage(yuv, ImageFormat.NV21, width, height, null)
                .compressToJpeg(Rect(0, 0, width, height), JPEG_QUALITY, out)
            return Base64.encodeToString(scaleDown(out.toByteArray()), Base64.NO_WRAP)
        } finally {
            camera.release()
            texture.release()
        }
    }

    /**
     * 按需降采样；解码失败时返回原图——原图是有效的，丢掉它等于白拍一次。
     */
    private fun scaleDown(jpeg: ByteArray): ByteArray {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, bounds)
        var sample = 1
        while (bounds.outWidth / sample > MAX_WIDTH) sample *= 2
        if (sample == 1) return jpeg

        val bitmap = BitmapFactory.decodeByteArray(
            jpeg,
            0,
            jpeg.size,
            BitmapFactory.Options().apply { inSampleSize = sample },
        ) ?: return jpeg
        return ByteArrayOutputStream().also { out ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
            bitmap.recycle()
        }.toByteArray()
    }
}
