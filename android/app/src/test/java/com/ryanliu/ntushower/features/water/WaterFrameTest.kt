package com.ryanliu.ntushower.features.water

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 帧/CRC/packid/反转/多帧/状态记录的实测回归。
 *
 * 向量全部来自 2026-10-29 在真机抓到的回包，没有手写帧：
 * - 1F `.50` 的 `F4 B1` 与 `F4 B3`（含浴室 App 轮询的多帧）；
 * - 1F 远程开 4 号的 `F4 B2` ack（`00 00 02 C1` = 705）；
 * - 1F 关闭 2/3 后的 `F4 B3`（packid 703）；
 * - 6F `.60` 的 24 门 `F4 B3`。
 */
class WaterFrameTest {

    @Test
    fun `open frame matches the app send vector`() {
        val send = WaterFrame.build(1, WaterFrame.openPayload(5), 1)
        val expected = hex("A4 61 01 00 00 00 05 AA 01 B2 F4 05 00 01 00 5A 02")
        assertArrayEquals(expected, send)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `build rejects pack ids outside the single byte range`() {
        // 255 会让“低字节 & 0xFF”与“16 位还原”出现两种读法；本实现不碰这个边界。
        WaterFrame.build(1, WaterFrame.openPayload(1), 255)
    }

    @Test
    fun `crc covers protocol id through pack id`() {
        val raw = hex("02 5A 00 01 00 05 F4 B2 01 AA 05 00 00 00 01 61 A4")
        assertEquals(0x61A4, WaterFrame.crc16(raw, 1, 14))
    }

    @Test
    fun `parses F4 B1 config frame with its 16 bit pack id`() {
        // 1F 实读：payload 00 B1 07 14（7 门 / 20 分钟），packid 301。
        val frames = WaterFrame.parseFrames(hex("56 55 2D 01 00 00 14 07 B1 00 04 00 01 00 A5 02"))
        assertEquals(1, frames.size)
        val frame = frames.single()
        assertEquals(WaterFrame.Kind.CONFIG, frame.kind)
        assertEquals(301, frame.packId)
        assertEquals(7, frame.doorNum)
        assertEquals(20, frame.bathMinutes)
    }

    @Test
    fun `parses the real F4 B2 open ack`() {
        // 1F 远程开 4 号后抓到的 ack：data 只有返回码 00，packid 705 = [00 00 02 C1]。
        val frames = WaterFrame.parseFrames(hex("35 B2 C1 02 00 00 00 01 00 01 00 A5 02"))
        assertEquals(1, frames.size)
        val frame = frames.single()
        assertEquals(WaterFrame.Kind.ACK, frame.kind)
        assertEquals(705, frame.packId)
        assertTrue(frame.accepted)
    }

    @Test
    fun `parses 1F status frame with the live bits`() {
        // 1F 关闭 2/3 后：door1 0x07，door2 0x01，door3 0x00，door5 0x01，door6 0x07。
        // 位义：0x01 占用、0x02 同步、0x04 现场开关（见 WaterFrame.DoorStatus）。
        val frames = WaterFrame.parseFrames(
            hex(
                "5A 3B BF 02 00 00 00 00 00 00 00 14 00 2C 00 07 00 00 13 06 01 00 00 00 00 00 00 00 " +
                    "00 00 00 5C 00 D2 08 01 2B 00 C2 00 07 05 07 10 00 27 00 01 00 A5 02",
            ),
        )
        assertEquals(1, frames.size)
        val frame = frames.single()
        assertEquals(WaterFrame.Kind.STATUS, frame.kind)
        assertEquals(703, frame.packId)
        assertEquals(7, frame.doors.size)
        assertEquals(0x07, frame.doors[0].status)
        assertEquals(0x01, frame.doors[1].status)
        assertEquals(0x00, frame.doors[2].status)
        assertEquals(0x01, frame.doors[4].status)
        assertEquals(0x07, frame.doors[5].status)
        assertTrue(frame.doors[0].bathSeconds > 0)
    }

    @Test
    fun `keeps scanning all frames in one read`() {
        // 1F 一次 read 里的 166 字节：我们的状态帧在前，后面还跟着浴室 App 轮询帧与
        // 一个 ack。解析器不能把“第一帧”当成唯一结果，也不能被后面的帧截断。
        val frames = WaterFrame.parseFrames(
            hex(
                "24 EB 2E 01 00 00 00 00 00 00 00 87 01 99 02 01 00 00 2E 0C 01 9B 00 63 01 01 00 " +
                    "00 00 00 00 44 01 6A 02 01 0A 01 34 02 01 05 07 10 00 27 00 01 00 A5 02 77 90 " +
                    "D3 1B 00 00 00 00 00 00 00 87 01 9A 02 01 00 00 2F 0C 01 9B 00 64 01 01 00 00 " +
                    "00 00 00 44 01 6B 02 01 0A 01 35 02 01 05 07 10 00 27 00 01 00 A5 02 8D F7 D4 " +
                    "1B 00 00 00 00 00 00 00 87 01 9B 02 01 00 00 30 0C 01 9B 00 65 01 01 00 00 00 " +
                    "00 00 44 01 6C 02 01 0A 01 36 02 01 05 07 10 00 27 00 01 00 A5 02 6B 59 D5 1B " +
                    "00 00 00 01 00 01 00 A5 02",
            ),
        )
        assertEquals(4, frames.size)
        assertEquals(302, frames[0].packId)
        assertEquals(WaterFrame.Kind.STATUS, frames[0].kind)
        assertEquals(WaterFrame.Kind.STATUS, frames[1].kind)
        assertEquals(WaterFrame.Kind.STATUS, frames[2].kind)
        assertEquals(WaterFrame.Kind.ACK, frames[3].kind)
    }

    @Test
    fun `parses 6F board-reported 24 doors while the site has only seven`() {
        // 6F 板自报 24 门，但现场实际只有 7 个喷头；前 7 条里 2/3/5 号在计时，其余空闲。
        val raw = hex(
            "9A 13 38 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 00 00 24 02 01 00 00 00 00 00 00 00 17 14 01 00 00 64 0E 01 00 00 00 00 00 05 18" +
                "10 00 7C 00 01 00 A5 02",
        )
        val frames = WaterFrame.parseFrames(raw)
        assertEquals(1, frames.size)
        val frame = frames.single()
        assertEquals(WaterFrame.Kind.STATUS, frame.kind)
        assertEquals(312, frame.packId)
        assertEquals(24, frame.doors.size)
        assertEquals(0, frame.doors[0].status)
        assertEquals(0x01, frame.doors[1].status)
        assertEquals(0x01, frame.doors[2].status)
        assertEquals(0, frame.doors[3].status)
        assertEquals(0x01, frame.doors[4].status)
        assertEquals(3670, frame.doors[1].bathSeconds)
        assertEquals(5123, frame.doors[2].bathSeconds)
        assertEquals(546, frame.doors[4].bathSeconds)
    }

    @Test
    fun `parses 5F board-reported 24 doors while the site has seven`() {
        // 5F 板自报 24 门/20 分钟是错的：现场实际只有 7 个喷头。前 7 门里 1、3、5
        // 有状态，8–24 全 0；解析器保留全部记录只为确认边界，业务只取 waterList 的 7 门。
        val raw = hex(
            "11 21 FD 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00" +
                "00 00 00 00 00 57 00 93 00 07 00 00 00 00 00 88 00 BA 01 01 00 00 00 00 00 00 00 00 00 02 05 18" +
                "10 00 7C 00 01 00 A5 02",
        )
        val frame = WaterFrame.parseFrames(raw).single()
        assertEquals(24, frame.doors.size)
        assertEquals(0x02, frame.doors[0].status)
        assertEquals(0x01, frame.doors[2].status)
        assertEquals(0x07, frame.doors[4].status)
        assertTrue((7 until 24).all { frame.doors[it].status == 0 })
        assertEquals(441, frame.doors[2].bathSeconds)
        assertEquals(136, frame.doors[2].allOpenSeconds)
    }

    @Test
    fun `rejects malformed normal frames without guessing`() {
        assertNull(WaterFrame.parseFrame(ByteArray(0)))
        assertNull(WaterFrame.parseFrame(ByteArray(12)))
        assertNull(WaterFrame.parseFrame(normal(byteArrayOf(0)).also { it[1] = 0x5A }))
        // length = 0：结构不成立，在 CRC 之前就拒绝
        assertNull(WaterFrame.parseFrame(byteArrayOf(0x02, 0xA5.toByte(), 0, 1, 0, 0, 0, 0, 0, 1, 0, 0)))
        // 声明长度与实长不一致
        assertNull(WaterFrame.parseFrame(normal(byteArrayOf(0, 0xB1.toByte(), 7, 20)).copyOf(10)))
        // F4 B3 的记录长度不是 5
        assertNull(WaterFrame.parseFrame(normal(byteArrayOf(0, 0x10, 1, 4, 0, 0, 0, 0, 0))))
    }

    @Test
    fun `scanning ignores a short or impossible frame boundary`() {
        assertTrue(WaterFrame.parseFrames(ByteArray(0)).isEmpty())
        assertTrue(WaterFrame.parseFrames(byteArrayOf(0xA5.toByte(), 0x02)).isEmpty())
        assertTrue(WaterFrame.parseFrames(byteArrayOf(0, 0, 0, 0, 0, 0xA5.toByte(), 0x02)).isEmpty())
        assertTrue(WaterFrame.parseFrames(byteArrayOf(0, 0, 10, 0, 0, 0, 0xA5.toByte(), 0x02)).isEmpty())
    }

    @Test
    fun `scanning tolerates bytes around a valid frame`() {
        val frame = hex("56 55 2D 01 00 00 14 07 B1 00 04 00 01 00 A5 02")
        val frames = WaterFrame.parseFrames(byteArrayOf(9, 9) + frame + byteArrayOf(9))
        assertEquals(1, frames.size)
        assertEquals(301, frames.single().packId)
    }

    @Test
    fun `response flags cover accepted and missing payload`() {
        val accepted = WaterFrame.Response(1, 1, WaterFrame.Kind.ACK, byteArrayOf(0), byteArrayOf(0))
        val rejected = WaterFrame.Response(1, 1, WaterFrame.Kind.ACK, byteArrayOf(7), byteArrayOf(7))
        val empty = WaterFrame.Response(1, 1, WaterFrame.Kind.ACK, ByteArray(0), ByteArray(0))

        assertTrue(accepted.accepted)
        assertEquals(0, accepted.returnCode)
        assertTrue(!rejected.accepted)
        assertEquals(7, rejected.returnCode)
        assertTrue(!empty.accepted)
        assertEquals(-1, empty.returnCode)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `build rejects an empty payload`() {
        WaterFrame.build(1, ByteArray(0), 1)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `build rejects an oversized payload`() {
        WaterFrame.build(1, ByteArray(256), 1)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `build rejects pack id zero`() {
        WaterFrame.build(1, WaterFrame.openPayload(1), 0)
    }

    @Test
    fun `rejects a frame with a bad crc`() {
        val raw = hex("89 83 01 00 00 00 14 07 B1 00 04 00 01 00 A5 02")
        // 反转帧的前两个字节就是 CRC（低、高）；改它才是真的 CRC 坏。
        raw[0] = (raw[0].toInt() xor 0x01).toByte()
        assertNull(WaterFrame.parseFrames(raw).firstOrNull { it.kind == WaterFrame.Kind.CONFIG })
    }

    @Test
    fun `does not treat a non matching pack id as own`() {
        val frames = WaterFrame.parseFrames(hex("56 55 2D 01 00 00 14 07 B1 00 04 00 01 00 A5 02"))
        assertEquals(301, frames.firstOrNull { it.packId == 301 }?.packId)
        assertNull(frames.firstOrNull { it.packId == 302 })
    }

    /** 一帧正常方向的回包：`02 A5 POS(2) 00 len payload packid(4) crc(2)`。 */
    private fun normal(payload: ByteArray, packId: Int = 1): ByteArray {
        val body = ByteArray(6 + payload.size + 4)
        body[0] = 0x02
        body[1] = 0xA5.toByte()
        body[2] = 0
        body[3] = 1
        body[4] = 0
        body[5] = payload.size.toByte()
        payload.copyInto(body, 6)
        body[6 + payload.size + 2] = (packId shr 8).toByte()
        body[6 + payload.size + 3] = packId.toByte()
        val crc = WaterFrame.crc16(body, 1, body.size - 1)
        return body + byteArrayOf((crc shr 8).toByte(), crc.toByte())
    }

    private fun hex(text: String): ByteArray =
        text.replace(" ", "").chunked(2).map { it.toInt(16).toByte() }.toByteArray()
}
