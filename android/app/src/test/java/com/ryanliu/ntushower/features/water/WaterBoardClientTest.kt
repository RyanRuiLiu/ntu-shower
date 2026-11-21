package com.ryanliu.ntushower.features.water

import com.ryanliu.ntushower.transport.ReasonCodes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

/**
 * 水控板 TCP 链路：真实回环连接，服务端按请求回放帧。
 *
 * 覆盖的不只是"能连上"：请求字节、packid 过滤、多帧、坏 CRC、整轮截止时间与
 * 连接失败映射都在这里。板子的真实回包格式已由 [WaterFrameTest] 的抓包向量钉住。
 */
class WaterBoardClientTest {

    private val board = WaterTargetResolver.Board(
        code = "0025",
        name = "啬园校区28号楼1层",
        ip = "127.0.0.1",
        port = 1,
        pos = 1,
        start = 1,
        count = 7,
    )

    @Test
    fun `open sends the AA flag and accepts the ack`() {
        val server = FakeBoardServer { conn, request ->
            write(conn, response(request, byteArrayOf(0)))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            val response = client.open(board.copy(port = server.port), boardDoor = 4)

            assertTrue(response.accepted)
            val sent = server.requests.single()
            assertEquals(1, ((sent[2].toInt() and 0xFF) shl 8) or (sent[3].toInt() and 0xFF))
            assertEquals("F4B201AA04", sent.copyOfRange(6, 11).toHex())
        } finally {
            server.close()
        }
    }

    @Test
    fun `close sends the 00 flag`() {
        val server = FakeBoardServer { conn, request ->
            write(conn, response(request, byteArrayOf(0)))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            client.close(board.copy(port = server.port), boardDoor = 6)

            assertEquals("F4B2010006", server.requests.single().copyOfRange(6, 11).toHex())
        } finally {
            server.close()
        }
    }

    @Test
    fun `config parses the board door count and max minutes`() {
        val server = FakeBoardServer { conn, request ->
            write(conn, response(request, byteArrayOf(0, 0xB1.toByte(), 7, 20)))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            val response = client.config(board.copy(port = server.port))

            assertEquals(WaterFrame.Kind.CONFIG, response.kind)
            assertEquals(7, response.doorNum)
            assertEquals(20, response.bathMinutes)
        } finally {
            server.close()
        }
    }

    @Test
    fun `status parses every returned door`() {
        val payload = byteArrayOf(0, 0x10, 7, 5) + doors(
            door(0x07, 194, 43),
            door(0x01, 2250, 92),
            door(0x00, 0, 0),
            door(0x02, 0, 0),
            door(0x01, 1549, 0),
            door(0x07, 44, 20),
            door(0x00, 0, 0),
        )
        val server = FakeBoardServer { conn, request ->
            write(conn, response(request, payload))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            val response = client.status(board.copy(port = server.port))

            assertEquals(WaterFrame.Kind.STATUS, response.kind)
            assertEquals(7, response.doors.size)
            assertEquals(0x07, response.doors[0].status)
            assertEquals(2250, response.doors[1].bathSeconds)
            assertEquals(20, response.doors[5].allOpenSeconds)
        } finally {
            server.close()
        }
    }

    @Test
    fun `skips frames that do not carry this pack id`() {
        val server = FakeBoardServer { conn, request ->
            val other = response(request, byteArrayOf(0), packIdOffset = 1)
            write(conn, other + response(request, byteArrayOf(0)))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            val response = client.open(board.copy(port = server.port), boardDoor = 2)

            assertTrue(response.accepted)
        } finally {
            server.close()
        }
    }

    @Test
    fun `a silent board maps to timeout`() {
        // 连接保持打开但一个字节也不发：客户端读超时，而不是读到 EOF
        val server = FakeBoardServer { conn, _ -> conn.getInputStream().read() }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 200)
            assertReason(ReasonCodes.WATER_TIMEOUT) {
                client.open(board.copy(port = server.port), boardDoor = 1)
            }
        } finally {
            server.close()
        }
    }

    @Test
    fun `broadcast frames do not extend the overall deadline`() {
        val server = FakeBoardServer { conn, request ->
            // 持续写别的 packid：每次 read 都能拿到字节，单次 SO_TIMEOUT 会被不断续命。
            // 只有整轮截止时间能让请求结束；写入在对端关闭后抛异常，循环随之退出。
            while (true) write(conn, response(request, byteArrayOf(0), packIdOffset = 1))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 200)
            val started = System.nanoTime()
            assertReason(ReasonCodes.WATER_TIMEOUT) {
                client.open(board.copy(port = server.port), boardDoor = 1)
            }
            val elapsedMs = (System.nanoTime() - started) / 1_000_000
            assertTrue("整轮等待被续命到 ${elapsedMs}ms", elapsedMs < 1_000)
        } finally {
            server.close()
        }
    }

    @Test
    fun `a bad crc frame is ignored`() {
        val server = FakeBoardServer { conn, request ->
            val frame = response(request, byteArrayOf(0))
            frame[0] = (frame[0].toInt() xor 0x01).toByte()
            write(conn, frame)
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 200)
            assertReason(ReasonCodes.WATER_TIMEOUT) {
                client.open(board.copy(port = server.port), boardDoor = 1)
            }
        } finally {
            server.close()
        }
    }

    @Test
    fun `a closed port maps to board unreachable`() {
        val port = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
        val client = WaterBoardClient(connectTimeoutMs = 200, readTimeoutMs = 200)
        assertReason(ReasonCodes.WATER_BOARD_UNREACHABLE) {
            client.open(board.copy(port = port), boardDoor = 1)
        }
    }

    @Test
    fun `the production constructor still maps a closed port`() {
        val port = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
        assertReason(ReasonCodes.WATER_BOARD_UNREACHABLE) {
            WaterBoardClient().open(board.copy(port = port), boardDoor = 1)
        }
    }

    @Test
    fun `a connection reset maps to board unreachable`() {
        val server = FakeBoardServer { conn, _ ->
            conn.setSoLinger(true, 0)
            conn.close()
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            assertReason(ReasonCodes.WATER_BOARD_UNREACHABLE) {
                client.open(board.copy(port = server.port), boardDoor = 1)
            }
        } finally {
            server.close()
        }
    }

    @Test
    fun `an io error after connect maps to board unreachable`() {
        // 请求写出后读回包阶段连接被对端中断（对端中途终止）：这不是超时——连不上与
        // "连上了但对端终止"是两种处境，前者才是 unreachable 之外的超时语义
        val server = FakeBoardServer { conn, _ ->
            conn.setSoLinger(true, 0)
            conn.close()
            // 写请求给假服务器自己用：真实客户端的请求发出后，读回包拿到的是 IOException
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            assertReason(ReasonCodes.WATER_BOARD_UNREACHABLE) {
                client.open(board.copy(port = server.port), boardDoor = 1)
            }
        } finally {
            server.close()
        }
    }

    @Test
    fun `pack id wraps around after the max`() {
        // 1..254 环回：序号随请求递增，到 254 后回到 1。断言只看**相邻关系**——
        // PACK_ID 是静态状态，起点取决于此前跑过多少请求，绝对值断不住。
        // 假服务器存的是反转回正常序的帧：packid 在 6+payload+3（单字节，高字节为 0）。
        val server = FakeBoardServer { conn, request ->
            write(conn, response(request, byteArrayOf(0)))
        }
        try {
            val client = WaterBoardClient(connectTimeoutMs = 500, readTimeoutMs = 500)
            fun requestPackId(index: Int): Int = server.requests[index][14].toInt() and 0xFF

            repeat(300) { client.open(board.copy(port = server.port), boardDoor = 1) }
            assertEquals(300, server.requests.size)
            var wraps = 0
            for (i in 1 until 300) {
                val previous = requestPackId(i - 1)
                val current = requestPackId(i)
                if (previous >= 254) {
                    wraps += 1
                    assertEquals(1, current)
                } else {
                    assertEquals(previous + 1, current)
                }
            }
            // 300 次请求必然至少跨过一次 254→1，环回不是纸面推演
            assertTrue("300 次请求应至少环回一次", wraps >= 1)
        } finally {
            server.close()
        }
    }

    private fun assertReason(expected: String, block: () -> Unit) {
        try {
            block()
            fail("应当抛出 $expected")
        } catch (e: WaterBoardException) {
            assertEquals(expected, e.reasonCode)
        }
    }

    private fun response(request: ByteArray, payload: ByteArray, packIdOffset: Int = 0): ByteArray {
        val length = request[5].toInt() and 0xFF
        val high = request[6 + length + 2].toInt() and 0xFF
        val low = request[6 + length + 3].toInt() and 0xFF
        val packId = (high shl 8 or low) + packIdOffset
        val body = ByteArray(6 + payload.size + 4)
        body[0] = 0x02
        body[1] = 0xA5.toByte()
        body[2] = request[2]
        body[3] = request[3]
        body[4] = 0
        body[5] = payload.size.toByte()
        payload.copyInto(body, 6)
        body[6 + payload.size + 2] = (packId shr 8).toByte()
        body[6 + payload.size + 3] = packId.toByte()
        val crc = WaterFrame.crc16(body, 1, body.size - 1)
        return (body + byteArrayOf((crc shr 8).toByte(), crc.toByte())).reversedArray()
    }

    private fun doors(vararg records: ByteArray): ByteArray = records.fold(ByteArray(0)) { acc, item -> acc + item }

    private fun door(status: Int, bath: Int, allOpen: Int): ByteArray = byteArrayOf(
        status.toByte(),
        (bath / 255).toByte(),
        (bath % 255).toByte(),
        (allOpen / 255).toByte(),
        (allOpen % 255).toByte(),
    )

    private fun write(conn: Socket, bytes: ByteArray) {
        conn.getOutputStream().apply {
            write(bytes)
            flush()
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02X".format(it) }

    private class FakeBoardServer(private val onRequest: (Socket, ByteArray) -> Unit) : AutoCloseable {
        private val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        val port: Int = server.localPort
        val requests = CopyOnWriteArrayList<ByteArray>()
        private val worker = Thread {
            while (!server.isClosed) {
                try {
                    server.accept().use { conn ->
                        conn.soTimeout = 1_000
                        val request = readRequest(conn) ?: return@use
                        requests.add(request)
                        onRequest(conn, request)
                    }
                } catch (_: Exception) {
                    // close() 后 accept/写入会抛，退出即可
                }
            }
        }.apply {
            isDaemon = true
            start()
        }

        private fun readRequest(conn: Socket): ByteArray? {
            // 线上是整帧反转：先到的 6 字节是 CRC + packid，长度字节在帧尾之前。
            // 测试里请求只有 2-5 字节 payload，逐字节读到结尾的 `5A 02` 即可。
            val input = conn.getInputStream()
            val frame = java.io.ByteArrayOutputStream()
            val first = readExactly(input, 6) ?: return null
            frame.write(first)
            while (true) {
                val one = input.read()
                if (one < 0) return null
                frame.write(one)
                val bytes = frame.toByteArray()
                if (bytes.size >= 2 &&
                    bytes[bytes.size - 2] == 0x5A.toByte() &&
                    bytes[bytes.size - 1] == 0x02.toByte()
                ) {
                    return bytes.reversedArray()
                }
                if (bytes.size > 300) return null
            }
        }

        private fun readExactly(input: java.io.InputStream, size: Int): ByteArray? {
            val out = ByteArray(size)
            var offset = 0
            while (offset < size) {
                val read = input.read(out, offset, size - offset)
                if (read < 0) return null
                offset += read
            }
            return out
        }

        override fun close() {
            server.close()
            worker.join(1_000)
        }
    }
}
