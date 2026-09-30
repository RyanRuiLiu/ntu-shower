package com.ryanliu.ntushower.core.dns

import okhttp3.Dns
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.Random

/**
 * 内置 DNS 解析器：设备系统 DNS 不可用（实测校园网封锁默认解析器，见 docs/ARCHITECTURE.md），
 * 直接向公共 DNS 发标准查询（UDP 优先，端口超时换 TCP，同为标准查询方式）。
 * 只解析服务域名——域名是设备唯一的网络锚点，无缓存、无兜底。
 */
class DnsResolver(
    private val server: String = "114.114.114.114",
    private val port: Int = 53,
    private val timeoutMs: Int = 5000,
) : Dns {

    override fun lookup(hostname: String): List<InetAddress> {
        val answer = query(hostname, overTcp = false) ?: query(hostname, overTcp = true)
        if (answer == null) {
            throw UnknownHostException("dns query failed: $hostname via $server")
        }
        return listOf(InetAddress.getByAddress(hostname, answer))
    }

    /** 返回第一个 A 记录的 4 字节地址；无结果或失败返回 null。 */
    private fun query(hostname: String, overTcp: Boolean): ByteArray? {
        var udp: DatagramSocket? = null
        var tcp: Socket? = null
        return try {
            val packet = buildQuery(hostname)
            val response: ByteArray = if (overTcp) {
                tcp = Socket()
                tcp.connect(InetSocketAddress(server, port), timeoutMs)
                tcp.soTimeout = timeoutMs
                val out = tcp.getOutputStream()
                out.write(byteArrayOf((packet.size shr 8).toByte(), packet.size.toByte()))
                out.write(packet)
                out.flush()
                val input = tcp.getInputStream()
                val len = (input.read() shl 8) or input.read()
                if (len <= 0) return null
                val buf = ByteArray(len)
                var off = 0
                while (off < len) {
                    val n = input.read(buf, off, len - off)
                    if (n < 0) return null
                    off += n
                }
                buf
            } else {
                udp = DatagramSocket()
                udp.soTimeout = timeoutMs
                udp.send(DatagramPacket(packet, packet.size, InetAddress.getByName(server), port))
                val buf = ByteArray(512)
                val p = DatagramPacket(buf, buf.size)
                udp.receive(p)
                buf.copyOf(p.length)
            }
            parseFirstA(response)
        } catch (e: SocketTimeoutException) {
            null
        } catch (e: Exception) {
            null
        } finally {
            try {
                udp?.close()
            } catch (ignored: Exception) {
                // socket 已死，无需处理
            }
            try {
                tcp?.close()
            } catch (ignored: Exception) {
                // socket 已死，无需处理
            }
        }
    }

    internal fun buildQuery(hostname: String): ByteArray {
        val labels = hostname.split(".")
        var size = 17 + hostname.length // 12 头 + 1 结尾 + 4 尾类型类 + 标签长度字节
        for (l in labels) size += 1
        val out = ByteArray(size)
        var i = 0
        fun put(b: Int) {
            out[i++] = b.toByte()
        }
        fun putShort(s: Int) {
            put(s shr 8)
            put(s and 0xFF)
        }
        Random().nextInt().let { putShort(it and 0xFFFF) } // id
        putShort(0x0100) // flags: RD
        putShort(1) // qdcount
        putShort(0)
        putShort(0)
        putShort(0)
        for (l in labels) {
            val bytes = l.toByteArray(Charsets.US_ASCII)
            put(bytes.size)
            System.arraycopy(bytes, 0, out, i, bytes.size)
            i += bytes.size
        }
        put(0)
        putShort(1) // QTYPE=A
        putShort(1) // QCLASS=IN
        return out.copyOf(i)
    }

    /** 跳过 question 段，遍历 answer 段找第一条 A 记录。 */
    internal fun parseFirstA(r: ByteArray): ByteArray? {
        if (r.size < 12) return null
        val rcode = r[3].toInt() and 0x0F
        if (rcode != 0) return null
        val ancount = ((r[6].toInt() and 0xFF) shl 8) or (r[7].toInt() and 0xFF)
        if (ancount == 0) return null
        var off = 12
        while (off < r.size && r[off].toInt() != 0) off += (r[off].toInt() and 0xFF) + 1
        off += 5 // root 标签 + QTYPE + QCLASS
        for (a in 0 until ancount) {
            if (off + 2 > r.size) return null
            off += if ((r[off].toInt() and 0xC0) == 0xC0) 2 else nameLen(r, off)
            if (off + 10 > r.size) return null
            val type = ((r[off].toInt() and 0xFF) shl 8) or (r[off + 1].toInt() and 0xFF)
            val rdlen = ((r[off + 8].toInt() and 0xFF) shl 8) or (r[off + 9].toInt() and 0xFF)
            off += 10
            if (type == 1 && rdlen == 4 && off + 4 <= r.size) {
                return byteArrayOf(r[off], r[off + 1], r[off + 2], r[off + 3])
            }
            off += rdlen
        }
        return null
    }

    private fun nameLen(r: ByteArray, start: Int): Int {
        var off = start
        while (off < r.size && r[off].toInt() != 0) off += (r[off].toInt() and 0xFF) + 1
        return off - start + 1
    }
}
