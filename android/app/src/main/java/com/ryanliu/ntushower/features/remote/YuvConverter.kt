package com.ryanliu.ntushower.features.remote

/**
 * 解码器输出色彩格式 → NV21 的纯转换。
 *
 * 独立成文件的原因：这段逻辑不依赖任何 Android 框架类（只操作 ByteArray），
 * 因此必须纳入 JVM 单测覆盖，而不是随 ScreenCapture 一起进入框架耦合豁免清单。
 */
object YuvConverter {

    /** MediaCodec 色彩格式：NV12（Y 平面 + 交错 UV）。 */
    const val COLOR_FORMAT_NV12 = 21

    /** MediaCodec 色彩格式：I420（Y 平面 + U 平面 + V 平面）。 */
    const val COLOR_FORMAT_I420 = 19

    /**
     * 把解码器输出统一转为 NV21（YuvImage 唯一接受的输入格式）。
     * 未知格式按 NV12 处理——真机实测该解码器输出的色彩格式即为 NV12。
     */
    fun toNv21(yuv: ByteArray, width: Int, height: Int, colorFormat: Int): ByteArray = when (colorFormat) {
        COLOR_FORMAT_I420 -> i420ToNv21(yuv, width, height)
        else -> swapUv(yuv, width * height)
    }

    /** I420（Y + U 平面 + V 平面）→ NV21（Y + 交错 VU）。 */
    fun i420ToNv21(yuv: ByteArray, width: Int, height: Int): ByteArray {
        val ySize = width * height
        val uvSize = (width / 2) * (height / 2)
        val result = ByteArray(ySize + uvSize * 2)
        System.arraycopy(yuv, 0, result, 0, ySize)
        for (i in 0 until uvSize) {
            result[ySize + i * 2] = yuv[ySize + uvSize + i] // V
            result[ySize + i * 2 + 1] = yuv[ySize + i] // U
        }
        return result
    }

    /** NV12（Y + 交错 UV）→ NV21：交换 UV 平面中每对字节的顺序，Y 平面原样保留。 */
    fun swapUv(data: ByteArray, uvStart: Int): ByteArray {
        val result = data.copyOf()
        var i = uvStart
        while (i < result.size - 1) {
            val u = result[i]
            result[i] = result[i + 1]
            result[i + 1] = u
            i += 2
        }
        return result
    }
}
