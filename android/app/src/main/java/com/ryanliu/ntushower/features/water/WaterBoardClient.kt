package com.ryanliu.ntushower.features.water

import com.ryanliu.ntushower.transport.ReasonCodes
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicInteger

/** 水控板请求失败：`reasonCode` 直接来自已登记的原因码。 */
class WaterBoardException(val reasonCode: String, message: String) : Exception(message)

/**
 * 板子的四条命令。抽成接口只为一件事：让 [WaterHandler] 的单测能注入固定回包，
 * 不必在测试里起真实 TCP；生产实现只有 [WaterBoardClient] 一个。
 */
internal interface WaterBoard {
    fun open(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response
    fun close(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response
    fun config(board: WaterTargetResolver.Board): WaterFrame.Response
    fun status(board: WaterTargetResolver.Board): WaterFrame.Response
}

/**
 * 水控板 TCP 客户端。
 *
 * ## 连接与超时
 *
 * 每次动作**短连接**：connect → 发一帧 → 收到匹配 packid 的回包 → 断开。连接
 * 2 秒、读 3 秒；只尝试一次，不自动重试、不自动补发断水——失败如实返回原因，由
 * 调用方决定下一步。板子是 USR-TCP232，浴室 App 也在同一条串行线上下发；一次
 * 读到的流里会混入 App 轮询的 `F4 B3` 帧，必须按 packid 过滤。读窗口比连接长，
 * 是因为串行线在 App 轮询时会有短暂排队；实测 2 秒曾在连续动作时误报超时。
 *
 * ## packid
 *
 * 原 App 的 packid 是 `[0, 0, n/255, n & 0xFF]`；这里用进程内递增的单字节序号
 * 1..254（见 [WaterFrame.build]），回包按同一个 16 位值选，不把"第一帧"当成
 * 自己的。只有 254 个值，但每次动作都是一条短连接、按序执行，够用。
 *
 * ## 状态位
 *
 * 客户端只返回板子的原始状态字节；把位翻译成“授权/按钮/出水”是 UI/调用方的事。
 * 位义与实测见 [WaterFrame.DoorStatus]：`0x01` 授权位、`0x02` 按钮位、
 * `0x04` 出水位，`0x07` 三者、`0x03` 授权+按钮（未出水）、`0x00` 关闭。
 * 注意 `open` 只置授权位，真正出水仍需现场按电磁阀；这是板子的硬件行为。
 */
class WaterBoardClient(private val connectTimeoutMs: Int = 2_000, private val readTimeoutMs: Int = 3_000) : WaterBoard {

    override fun open(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response =
        request(board, WaterFrame.openPayload(boardDoor), WaterFrame.Kind.ACK)

    override fun close(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response =
        request(board, WaterFrame.closePayload(boardDoor), WaterFrame.Kind.ACK)

    override fun config(board: WaterTargetResolver.Board): WaterFrame.Response =
        request(board, WaterFrame.configPayload(), WaterFrame.Kind.CONFIG)

    override fun status(board: WaterTargetResolver.Board): WaterFrame.Response =
        request(board, WaterFrame.statusPayload(), WaterFrame.Kind.STATUS)

    private fun request(
        board: WaterTargetResolver.Board,
        payload: ByteArray,
        expectedKind: WaterFrame.Kind,
    ): WaterFrame.Response {
        val packId = nextPackId()
        val send = WaterFrame.build(board.pos, payload, packId)
        val socket = connect(board)
        val received: WaterFrame.Response? = try {
            socket.getOutputStream().apply {
                write(send)
                flush()
            }
            awaitResponse(socket, packId, expectedKind)
        } catch (e: SocketTimeoutException) {
            null
        } catch (e: IOException) {
            fail(ReasonCodes.WATER_BOARD_UNREACHABLE, "水控板通信异常：${e.message}")
        } finally {
            try {
                socket.close()
            } catch (_: IOException) {
                // 关闭失败不影响已拿到的结果
            }
        }
        return received ?: fail(ReasonCodes.WATER_TIMEOUT, "水控板未在 ${readTimeoutMs}ms 内给出匹配的回应")
    }

    /**
     * 建立短连接。连接阶段的超时/拒绝都归 `board_unreachable`——连不上与"连上了但
     * 板子不回"是两种处境，后者才是 `water.timeout`。
     */
    private fun connect(board: WaterTargetResolver.Board): Socket {
        val socket = Socket()
        return try {
            socket.connect(InetSocketAddress(board.ip, board.port), connectTimeoutMs)
            socket
        } catch (e: IOException) {
            try {
                socket.close()
            } catch (_: IOException) {
                // 连接失败后的关闭失败没有可做的
            }
            fail(ReasonCodes.WATER_BOARD_UNREACHABLE, "水控板 ${board.ip}:${board.port} 连接失败：${e.message}")
        }
    }

    /**
     * 在整轮截止时间内读回包；读到匹配的帧立即返回，超时/连接被对端关闭返回 null。
     *
     * SO_TIMEOUT 只约束单次 read；板子会持续广播浴室 App 的轮询帧，若只靠它，
     * 每秒一帧就能让循环永远不超时。这里用截止时间约束**整轮**等待。
     */
    private fun awaitResponse(socket: Socket, packId: Int, expectedKind: WaterFrame.Kind): WaterFrame.Response? {
        val buffer = ByteArray(1024)
        val collected = ByteArrayOutputStream()
        val input = socket.getInputStream()
        val deadline = System.nanoTime() + readTimeoutMs * 1_000_000L
        while (true) {
            val remainingMs = (deadline - System.nanoTime()) / 1_000_000L
            if (remainingMs <= 0) return null
            socket.soTimeout = remainingMs.coerceAtMost(Int.MAX_VALUE.toLong()).toInt()
            val read = input.read(buffer)
            if (read <= 0) return null
            collected.write(buffer, 0, read)
            val frame = WaterFrame.parseFrames(collected.toByteArray())
                .firstOrNull { it.packId == packId && it.kind == expectedKind }
            if (frame != null) return frame
        }
    }

    private fun fail(reasonCode: String, message: String): Nothing = throw WaterBoardException(reasonCode, message)

    // API 19 没有 AtomicInteger.getAndUpdate（API 24 才有）；用 get + CAS 实现同样的
    // “取旧值、写入 1..254 的下一个值”，并保持并发下不丢序号。
    private fun nextPackId(): Int {
        while (true) {
            val current = PACK_ID.get()
            val next = if (current >= MAX_PACK_ID) 1 else current + 1
            if (PACK_ID.compareAndSet(current, next)) return current
        }
    }

    private companion object {
        const val MAX_PACK_ID = 254
        val PACK_ID = AtomicInteger(1)
    }
}
