package com.ryanliu.ntushower.core.dns

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream

/**
 * DNS 报文层的边界测试（socket 链路见 [DnsResolverTest] / [DnsResolverLookupTest]）。
 *
 * 报文构造与解析是整个自定义解析器的风险所在：校园网封锁默认解析器，
 * 域名解析是设备唯一的网络锚点，解析错等于设备失联。
 */
class DnsResolverPacketTest {

    private val resolver = DnsResolver()

    // ── buildQuery ──

    @Test
    fun `query encodes hostname as length-prefixed labels`() {
        val query = resolver.buildQuery("a.bc")

        // 12 头 + [1]a + [2]bc + [0] + 4（QTYPE/QCLASS）
        assertEquals(12 + 2 + 3 + 1 + 4, query.size)
        assertEquals(1, query[12].toInt())
        assertEquals('a'.code, query[13].toInt())
        assertEquals(2, query[14].toInt())
        assertEquals('b'.code, query[15].toInt())
        assertEquals('c'.code, query[16].toInt())
        assertEquals(0, query[17].toInt())
    }

    @Test
    fun `query ids differ across calls`() {
        // 固定 id 会让并发的响应无法区分
        val ids = (1..20).map { resolver.buildQuery("example.com").copyOfRange(0, 2).toList() }

        assertTrue("id 应随机", ids.toSet().size > 1)
    }

    // ── parseFirstA 的拒绝路径 ──

    @Test
    fun `rejects responses shorter than the header`() {
        assertNull(resolver.parseFirstA(ByteArray(11)))
    }

    @Test
    fun `rejects an empty buffer`() {
        assertNull(resolver.parseFirstA(ByteArray(0)))
    }

    @Test
    fun `rejects non-zero response codes`() {
        // NXDOMAIN 必须如实返回 null，不能给出任何地址
        assertNull(resolver.parseFirstA(dnsResponse(rcode = 3, answers = listOf(aAnswer("1.2.3.4")))))
    }

    @Test
    fun `rejects responses without answers`() {
        assertNull(resolver.parseFirstA(dnsResponse(answers = emptyList())))
    }

    @Test
    fun `skips non-A records`() {
        val response = dnsResponse(answers = listOf(typedAnswer(type = 28), aAnswer("5.6.7.8")))

        assertArrayEquals(byteArrayOf(5, 6, 7, 8), resolver.parseFirstA(response))
    }

    @Test
    fun `skips A records whose declared length is not four`() {
        val response = dnsResponse(answers = listOf(aAnswer("9.9.9.9", declaredLength = 3)))

        assertNull(resolver.parseFirstA(response))
    }

    @Test
    fun `returns null when the answer section is truncated`() {
        val full = dnsResponse(answers = listOf(aAnswer("1.2.3.4")))

        assertNull(resolver.parseFirstA(full.copyOf(full.size - 2)))
    }

    @Test
    fun `returns null when an answer header crosses the buffer edge`() {
        // 声明两条 answer 但只给一条的字节：第二条的 2 字节名字头与 10 字节固定段
        // 各自越界都归 null——解析器按声明计数消费，越界即中止，不读缓冲区外
        val one = aAnswer("1.2.3.4")
        val full = dnsResponse(answers = listOf(aAnswer("9.9.9.9"), aAnswer("1.2.3.4")))

        assertNull(resolver.parseFirstA(full.copyOf(full.size - one.size - 1)))
        assertNull(resolver.parseFirstA(full.copyOf(full.size - one.size - 9)))
    }

    // ── parseFirstA 的接受路径 ──

    @Test
    fun `handles compressed answer names`() {
        val response = dnsResponse(answers = listOf(aAnswer("10.0.0.1", compressed = true)))

        assertArrayEquals(byteArrayOf(10, 0, 0, 1), resolver.parseFirstA(response))
    }

    @Test
    fun `parses when the question section has multiple labels`() {
        val response = dnsResponse(
            question = "a.very-long-subdomain.example.com",
            answers = listOf(aAnswer("8.8.8.8")),
        )

        assertNotNull(resolver.parseFirstA(response))
    }

    @Test
    fun `parses an uncompressed answer name`() {
        // 未用名字指针时需逐标签跳过（走 nameLen 分支），真实应答两种都可能出现
        val response = dnsResponse(answers = listOf(aAnswer("7.7.7.7", compressed = false)))

        assertArrayEquals(byteArrayOf(7, 7, 7, 7), resolver.parseFirstA(response))
    }

    @Test
    fun `parses an answer whose name has multiple labels`() {
        val response = dnsResponse(
            answers = listOf(multiLabelAnswer("sub.example.com", "6.6.6.6")),
        )

        assertArrayEquals(byteArrayOf(6, 6, 6, 6), resolver.parseFirstA(response))
    }

    @Test
    fun `takes the first A record among several`() {
        val response = dnsResponse(
            answers = listOf(typedAnswer(type = 5), aAnswer("1.1.1.1"), aAnswer("2.2.2.2")),
        )

        assertArrayEquals(byteArrayOf(1, 1, 1, 1), resolver.parseFirstA(response))
    }
}

// ── socket 异常路径 ──

/** 接受连接后立即关闭：模拟对端异常（读长度前缀时即 EOF）。 */
internal class ClosingDnsTcpServer : Thread() {
    private val server = java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"))
    val port: Int = server.localPort

    @Volatile private var running = true

    init {
        isDaemon = true
    }

    override fun run() {
        while (running) {
            try {
                server.accept().close()
            } catch (_: Exception) { /* close() 后退出 */ }
        }
    }

    fun close() {
        running = false
        server.close()
    }
}

class DnsResolverFailureTest {

    @Test
    fun `lookup fails when tcp peer closes immediately`() {
        // UDP 无监听、TCP 接受后立刻关闭：两条路径都拿不到应答
        val server = ClosingDnsTcpServer()
        server.start()
        try {
            val resolver = DnsResolver(server = "127.0.0.1", port = server.port, timeoutMs = 500)
            try {
                resolver.lookup("host.example.com")
                org.junit.Assert.fail("expected UnknownHostException")
            } catch (expected: java.net.UnknownHostException) {
                // 预期路径：读长度前缀时 EOF
            }
        } finally {
            server.close()
        }
    }
}

// ── 报文构造辅助（RFC 1035 线上格式）──

private const val HEADER_SIZE = 12

private fun dnsResponse(
    rcode: Int = 0,
    question: String = "example.com",
    answers: List<ByteArray> = emptyList(),
): ByteArray {
    val out = ByteArrayOutputStream()
    val header = ByteArray(HEADER_SIZE)
    header[2] = 0x81.toByte() // QR + RD + RA
    header[3] = rcode.toByte()
    header[7] = answers.size.toByte()
    out.write(header)
    question.split(".").forEach { label ->
        out.write(label.length)
        out.write(label.toByteArray(Charsets.US_ASCII))
    }
    out.write(0) // root
    out.write(byteArrayOf(0, 1, 0, 1)) // QTYPE=A, QCLASS=IN
    answers.forEach { out.write(it) }
    return out.toByteArray()
}

private fun resourceRecord(name: ByteArray, type: Int, rdata: ByteArray, declaredLength: Int): ByteArray {
    val out = ByteArrayOutputStream()
    out.write(name)
    out.write(byteArrayOf((type shr 8).toByte(), type.toByte()))
    out.write(byteArrayOf(0, 1)) // CLASS=IN
    out.write(byteArrayOf(0, 0, 0, 60)) // TTL
    out.write(byteArrayOf((declaredLength shr 8).toByte(), declaredLength.toByte()))
    out.write(rdata)
    return out.toByteArray()
}

private fun aAnswer(address: String, compressed: Boolean = false, declaredLength: Int = 4): ByteArray {
    val name = if (compressed) byteArrayOf(0xC0.toByte(), 0x0C) else byteArrayOf(0)
    val rdata = address.split(".").map { it.toInt().toByte() }.toByteArray()
    return resourceRecord(name, type = 1, rdata = rdata, declaredLength = declaredLength)
}

private fun typedAnswer(type: Int): ByteArray = resourceRecord(byteArrayOf(0), type, ByteArray(4), 4)

/** 未压缩的多标签名字：每个标签带长度前缀并以根标签结尾（触发逐标签跳过路径）。 */
private fun multiLabelAnswer(hostname: String, address: String): ByteArray {
    val name = ByteArrayOutputStream().apply {
        hostname.split(".").forEach { label ->
            write(label.length)
            write(label.toByteArray(Charsets.US_ASCII))
        }
        write(0)
    }.toByteArray()
    val rdata = address.split(".").map { it.toInt().toByte() }.toByteArray()
    return resourceRecord(name, type = 1, rdata = rdata, declaredLength = 4)
}
