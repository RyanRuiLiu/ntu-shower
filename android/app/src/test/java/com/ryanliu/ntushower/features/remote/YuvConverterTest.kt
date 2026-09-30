package com.ryanliu.ntushower.features.remote

import org.junit.Assert.assertArrayEquals
import org.junit.Test

class YuvConverterTest {

    @Test
    fun `swapUv swaps each uv pair and keeps y plane untouched`() {
        val data = byteArrayOf(1, 2, 10, 20, 30, 40) // Y(2 bytes) + two UV pairs

        val result = YuvConverter.swapUv(data, uvStart = 2)

        assertArrayEquals(byteArrayOf(1, 2, 20, 10, 40, 30), result)
    }

    @Test
    fun `swapUv returns a copy without mutating input`() {
        val data = byteArrayOf(1, 2, 10, 20)

        YuvConverter.swapUv(data, uvStart = 2)

        assertArrayEquals(byteArrayOf(1, 2, 10, 20), data)
    }

    @Test
    fun `i420ToNv21 interleaves v then u after the y plane`() {
        // 2x2 frame: 4 luma bytes, then U plane (1 byte), then V plane (1 byte)
        val yuv = byteArrayOf(1, 2, 3, 4, 5, 6)

        val nv21 = YuvConverter.i420ToNv21(yuv, width = 2, height = 2)

        assertArrayEquals(byteArrayOf(1, 2, 3, 4, 6, 5), nv21)
    }

    @Test
    fun `toNv21 applies uv swap for nv12`() {
        val yuv = byteArrayOf(1, 2, 3, 4, 5, 6)

        val nv21 = YuvConverter.toNv21(yuv, 2, 2, YuvConverter.COLOR_FORMAT_NV12)

        assertArrayEquals(byteArrayOf(1, 2, 3, 4, 6, 5), nv21)
    }

    @Test
    fun `toNv21 reorders planes for i420`() {
        val yuv = byteArrayOf(1, 2, 3, 4, 5, 6)

        val nv21 = YuvConverter.toNv21(yuv, 2, 2, YuvConverter.COLOR_FORMAT_I420)

        assertArrayEquals(YuvConverter.i420ToNv21(yuv, 2, 2), nv21)
    }

    @Test
    fun `toNv21 falls back to nv12 for unknown color format`() {
        val yuv = byteArrayOf(1, 2, 3, 4, 5, 6)

        val nv21 = YuvConverter.toNv21(yuv, 2, 2, colorFormat = 9999)

        assertArrayEquals(YuvConverter.swapUv(yuv, 4), nv21)
    }
}
