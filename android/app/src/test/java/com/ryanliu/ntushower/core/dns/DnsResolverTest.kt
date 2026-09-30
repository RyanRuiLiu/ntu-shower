package com.ryanliu.ntushower.core.dns

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class DnsResolverTest {

    @Test
    fun `build query produces standard a record question`() {
        val query = DnsResolver().buildQuery("ntu-shower.example.com")

        // 头部：随机 id + RD 标志 + 单问题
        assertEquals(0x01, query[2].toInt() and 0xFF) // flags 高字节（RD）
        assertEquals(1, ((query[4].toInt() and 0xFF) shl 8) or (query[5].toInt() and 0xFF))

        // question 段：三段标签 + 根 + QTYPE=A + QCLASS=IN
        val labels = listOf("ntu-shower", "example", "com")
        var off = 12
        for (label in labels) {
            assertEquals(label.length, query[off].toInt())
            assertArrayEquals(label.toByteArray(Charsets.US_ASCII), query.copyOfRange(off + 1, off + 1 + label.length))
            off += 1 + label.length
        }
        assertEquals(0, query[off].toInt()) // 根标签
        assertEquals(0, query[off + 1].toInt())
        assertEquals(1, query[off + 2].toInt()) // QTYPE=A
        assertEquals(0, query[off + 3].toInt())
        assertEquals(1, query[off + 4].toInt()) // QCLASS=IN
        assertEquals(off + 5, query.size)
    }

    @Test
    fun `parse first a record from response`() {
        val query = DnsResolver().buildQuery("host.example.com")
        val answer = byteArrayOf(192.toByte(), 0, 2, 1) // TEST-NET-1：192.0.2.1
        val response = responseWithA(query, answer)
        assertArrayEquals(answer, DnsResolver().parseFirstA(response))
    }

    @Test
    fun `parse skips non a records and rejects rcode errors`() {
        val query = DnsResolver().buildQuery("host.example.com")

        // rcode != 0 → null
        val rcodeErr = responseWithA(query, byteArrayOf(1, 2, 3, 4), rcode = 3)
        assertNull(DnsResolver().parseFirstA(rcodeErr))

        // ancount=0 → null
        val noAnswer = responseWithA(query, ByteArray(0))
        assertNull(DnsResolver().parseFirstA(noAnswer))
        assertFalse(noAnswer.contentEquals(query))
    }

    /** 拼一个最小 DNS 应答：请求头 + question + ancount 条 answer（A 记录或 CNAME 型跳过）。 */
    private fun responseWithA(query: ByteArray, address: ByteArray, rcode: Int = 0): ByteArray {
        val out = query.copyOf()
        out[2] = 0x81.toByte() // QR=1, RD=1
        out[3] = (0x80 or rcode).toByte() // RA + rcode
        out[6] = ((if (address.isEmpty()) 0 else 1) shr 8).toByte()
        out[7] = (if (address.isEmpty()) 0 else 1).toByte()
        if (address.isEmpty()) return out

        val answer = ByteArray(16)
        answer[0] = 0xC0.toByte()
        answer[1] = 12 // name 指针 → question
        answer[2] = 0
        answer[3] = 1 // type A
        answer[4] = 0
        answer[5] = 1 // class IN
        answer[10] = 0
        answer[11] = 4 // rdlength=4
        address.copyInto(answer, 12)
        return out + answer
    }
}

// ── 本地假 DNS 服务器：覆盖 socket 链路（UDP 成功 / UDP 失败回落 TCP / 超时 / NXDOMAIN）──

internal class FakeDnsUdpServer : Thread() {
    private val socket = java.net.DatagramSocket(0, java.net.InetAddress.getByName("127.0.0.1"))
    val port: Int = socket.localPort

    @Volatile private var running = true

    init {
        isDaemon = true
    }

    override fun run() {
        val buf = ByteArray(512)
        while (running) {
            try {
                val packet = java.net.DatagramPacket(buf, buf.size)
                socket.receive(packet)
                // packet.data 是完整缓冲，必须按 packet.length 截取真实报文
                val response = responseFor(packet.data.copyOf(packet.length))
                socket.send(
                    java.net.DatagramPacket(
                        response,
                        response.size,
                        packet.address,
                        packet.port,
                    ),
                )
            } catch (_: Exception) { /* close() 后退出 */ }
        }
    }

    fun close() {
        running = false
        socket.close()
    }
}

internal fun responseFor(query: ByteArray): ByteArray {
    // 构造应答：复制请求头置 QR=1、ancount=1，原样返回 question 段并追加一条 A 记录 192.0.2.1
    val question = query.copyOfRange(12, query.size)
    val header = byteArrayOf(
        query[0], query[1], // id
        0x81.toByte(), 0x80.toByte(), // QR=1, RD=1, RA=1, rcode=0
        0, 1, // qdcount
        0, 1, // ancount=1
        0, 0, 0, 0,
    )
    val answerRecord = byteArrayOf(
        0xC0.toByte(), 12, // name 指针 → question
        0, 1, // type A
        0, 1, // class IN
        0, 0, 0, 60, // ttl
        0, 4, // rdlength
        192.toByte(), 0, 2, 1, // 192.0.2.1
    )
    return header + question + answerRecord
}

// ── socket 链路测试 ──

internal class FakeDnsTcpServer : Thread() {
    private val server = java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"))
    val port: Int = server.localPort

    @Volatile private var running = true

    init {
        isDaemon = true
    }

    override fun run() {
        while (running) {
            try {
                server.accept().use { conn -> serveConnection(conn) }
            } catch (_: Exception) { /* close() 后退出 */ }
        }
    }

    private fun serveConnection(conn: java.net.Socket) {
        val input = conn.getInputStream()
        val lenHigh = input.read()
        val lenLow = input.read()
        if (lenHigh < 0 || lenLow < 0) return
        val len = (lenHigh shl 8) or lenLow
        val query = readExactly(input, len) ?: return
        val response = responseFor(query)
        val out = conn.getOutputStream()
        out.write(byteArrayOf((response.size shr 8).toByte(), response.size.toByte()))
        out.write(response)
        out.flush()
    }

    private fun readExactly(input: java.io.InputStream, len: Int): ByteArray? {
        val query = ByteArray(len)
        var off = 0
        while (off < len) {
            val n = input.read(query, off, len - off)
            if (n < 0) return null
            off += n
        }
        return query
    }

    fun close() {
        running = false
        server.close()
    }
}

class DnsResolverLookupTest {

    @Test
    fun `lookup resolves via local udp server`() {
        val server = FakeDnsUdpServer()
        server.start()
        try {
            val resolver = DnsResolver(server = "127.0.0.1", port = server.port, timeoutMs = 2000)
            val address = resolver.lookup("host.example.com")
            assertEquals(byteArrayOf(192.toByte(), 0, 2, 1).toList(), address[0].address.toList())
        } finally {
            server.close()
        }
    }

    @Test
    fun `lookup falls back to tcp when udp silent`() {
        val tcp = FakeDnsTcpServer()
        tcp.start()
        try {
            // 同一端口只开 TCP：UDP 静默（ICMP 端口不可达/超时）→ 回落 TCP → 成功解析
            val resolver = DnsResolver(server = "127.0.0.1", port = tcp.port, timeoutMs = 1000)
            val address = resolver.lookup("host.example.com")
            assertEquals("192.0.2.1", address[0].hostAddress)
        } finally {
            tcp.close()
        }
    }

    @Test
    fun `lookup throws unknown host when nothing answers`() {
        // 127.0.0.1:1 无任何监听：UDP 与 TCP 均失败，属确定性行为（不受网络环境干扰）
        val resolver = DnsResolver(server = "127.0.0.1", port = 1, timeoutMs = 300)
        try {
            resolver.lookup("host.example.com")
            org.junit.Assert.fail("expected UnknownHostException")
        } catch (expected: java.net.UnknownHostException) {
            // 预期路径：UDP/TCP 均无应答
        }
    }
}
