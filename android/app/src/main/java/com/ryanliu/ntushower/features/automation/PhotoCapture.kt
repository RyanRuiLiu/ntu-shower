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
import java.io.ByteArrayOutputStream

/**
 * 拍照：Camera1 API，预览帧单次捕获 → NV21 → JPEG → Base64。
 * Camera.open 在无 Looper 线程调用，回调投递主线程不会被阻塞。
 */
object PhotoCapture {

    private const val TAG = "PhotoCapture"
    private const val MAX_WIDTH = 1280

    /** 返回 JPEG Base64；失败返回 null。 */
    fun takePhoto(): String? = try {
        captureInternal()
    } catch (e: Exception) {
        Log.e(TAG, "photo failed: ${e.message}", e)
        null
    }

    private fun captureInternal(): String? {
        val camera = Camera.open(0)
        try {
            val texture = SurfaceTexture(0)
            texture.setDefaultBufferSize(MAX_WIDTH, 720)
            camera.setPreviewTexture(texture)
            val params = camera.parameters
            val size = params.supportedPreviewSizes
                .filter { it.width <= MAX_WIDTH }
                .maxByOrNull { it.width * it.height } ?: params.previewSize
            size?.let { params.setPreviewSize(it.width, it.height) }
            camera.parameters = params
            var captured: ByteArray? = null
            camera.setOneShotPreviewCallback { data, camera1 ->
                captured = data
                camera1.stopPreview()
            }
            camera.startPreview()
            val deadline = System.currentTimeMillis() + 3000L
            while (captured == null && System.currentTimeMillis() < deadline) {
                Thread.sleep(50)
            }
            camera.stopPreview()
            val yuv = captured ?: return null
            val width = size?.width ?: 640
            val height = size?.height ?: 480
            val yuvImage = YuvImage(yuv, ImageFormat.NV21, width, height, null)
            val out = ByteArrayOutputStream()
            yuvImage.compressToJpeg(Rect(0, 0, width, height), 60, out)
            val jpeg = scaleDown(out.toByteArray(), MAX_WIDTH) ?: return null
            return Base64.encodeToString(jpeg, Base64.NO_WRAP)
        } finally {
            camera.release()
            Log.i(TAG, "camera released")
        }
    }

    private fun scaleDown(jpeg: ByteArray, maxWidth: Int): ByteArray? {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size, options)
        var sample = 1
        while (options.outWidth / sample > maxWidth) sample *= 2
        if (sample == 1) return jpeg
        val bitmap = BitmapFactory.decodeByteArray(
            jpeg,
            0,
            jpeg.size,
            BitmapFactory.Options().apply { inSampleSize = sample },
        )
        val scaledOut = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, 60, scaledOut)
        bitmap.recycle()
        return scaledOut.toByteArray()
    }
}
