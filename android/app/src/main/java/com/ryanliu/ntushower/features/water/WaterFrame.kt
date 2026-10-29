package com.ryanliu.ntushower.features.water

/**
 * 水控板帧编解码（纯 Kotlin，无 Android / 网络依赖）。
 *
 * ## 帧形状（来自浴室 App 的 `CommuPackage` 与实测回包）
 *
 * 正常帧：`02 5A POS(2) 00 len payload packid(4) crc(2)`；**发送前整帧反转**，
 * 板子回包也是反转的。`len` 是 payload 长度，帧总长 = `12 + len`。
 *
 * 两个容易写错的点，全部来自原 App：
 * - 长度/时长的两个字节按 `high * 255 + low` 合成（不是 256）；
 * - packid 四字节为 `[0, 0, packid / 255, packid & 0xFF]`——低字节是**整字节**
 *   （可以是 255），不是模 255。回包把这四个字节原样带回，解析按
 *   `byte2 * 256 + byte3` 还原成序号（实测 `[00 00 02 C1]` 对应发起方的 705）。
 *
 * 本实现只使用 1..254 这 254 个序号：高字节恒为 0，低字节就是序号本身，
 * 因此构建与回包过滤不存在 255/256 的歧义（见 [build]）。
 *
 * CRC 为 CRC-16/CCITT（初值 0、多项式 0x1021），覆盖正常帧的 `1..len+9`
 * 字节（从 `5A` 到 packid 最后一个字节），高字节在前。
 *
 * ## 回包多帧
 *
 * 板子会把别的客户端（浴室 App 轮询）的状态帧一起广播过来；一次 TCP 读到的流里
 * 可能有多帧。这里只做**帧边界与解析**，按 packid 选自己那一帧由
 * [WaterBoardClient] 负责——不要在解析器里假设"一次 read 就是一帧"。
 *
 * 门序：`F4 B3` 的每条记录按板内门号 1..N 排列；`DoorStatus.boardDoor` 就是
 * 这个 1-based 门号。逻辑门号 = `waterList.startnumber + boardDoor - 1`，由
 * [WaterTargetResolver] 换算。
 */
object WaterFrame {

    const val STX = 0x02
    const val PROTOCOL_ID = 0x5A
    const val RESPONSE_ID = 0xA5

    const val CMD_CONFIG = 0xB1
    const val CMD_SWITCH = 0xB2
    const val CMD_STATUS = 0xB3

    const val OPEN_FLAG = 0xAA
    const val CLOSE_FLAG = 0x00
    const val STATUS_MARKER = 0x10
    const val STATUS_RECORD_LEN = 5

    private const val HEADER_LEN = 6
    private const val PACK_ID_LEN = 4
    private const val CRC_LEN = 2

    enum class Kind { ACK, CONFIG, STATUS }

    /**
     * 板内一个喷头的状态。
     *
     * `status` 位义来自反编译的 `STATUS_door_bits.md` / `DoorStatusConfirmThread`，
     * 并与 1F 2/3 号、远程 4 号、2F 1 号的实测一致：
     * - `0x01` 占用（板子登记/使能）；
     * - `0x02` 水控板被动同步；
     * - `0x04` **现场开关**（人按下电磁阀才置位；`F4 B2 AA` 不置这一位）；
     * - `0x07` = 三者都有；`0x03` = 占用+同步（现场开关未按/无水）；`0x00` = 无占用。
     *
     * 因此 `F4 B2 AA` 只登记占用，**不直接出水**；真正出水仍要现场按电磁阀。
     * `bathSeconds` 是已洗浴时间、`allOpenSeconds` 是累计开阀时间（源码注释），
     * 均按 `high*255+low` 解析；远程登记时 `allOpenSeconds` 不增长。
     */
    data class DoorStatus(val boardDoor: Int, val status: Int, val bathSeconds: Int, val allOpenSeconds: Int)

    data class Response(
        val packId: Int,
        val pos: Int,
        val kind: Kind,
        val data: ByteArray,
        val raw: ByteArray,
        val doorNum: Int? = null,
        val bathMinutes: Int? = null,
        val doors: List<DoorStatus> = emptyList(),
    ) {
        /** `F4 B2` 回包：`data[0] == 0` 才是板子接受。 */
        val accepted: Boolean get() = data.isNotEmpty() && (data[0].toInt() and 0xFF) == 0

        /** `F4 B1` / `F4 B3` 的返回码；`data[0]` 非 0 时板子拒绝了读。 */
        val returnCode: Int get() = if (data.isEmpty()) -1 else data[0].toInt() and 0xFF
    }

    /** 开阀 payload：`F4 B2 01 AA <boardDoor>`。 */
    fun openPayload(boardDoor: Int): ByteArray = byteArrayOf(
        0xF4.toByte(),
        CMD_SWITCH.toByte(),
        1,
        OPEN_FLAG.toByte(),
        boardDoor.toByte(),
    )

    /** 关阀 payload：`F4 B2 01 00 <boardDoor>`。 */
    fun closePayload(boardDoor: Int): ByteArray = byteArrayOf(
        0xF4.toByte(),
        CMD_SWITCH.toByte(),
        1,
        CLOSE_FLAG.toByte(),
        boardDoor.toByte(),
    )

    /** 读板配置：`F4 B1`。 */
    fun configPayload(): ByteArray = byteArrayOf(0xF4.toByte(), CMD_CONFIG.toByte())

    /** 读板状态：`F4 B3`。 */
    fun statusPayload(): ByteArray = byteArrayOf(0xF4.toByte(), CMD_STATUS.toByte())

    /**
     * 构建并返回**已反转**的发送帧。
     *
     * packid 由调用方给；同一个连接/同一轮命令用不同 packid，回包按它过滤。
     */
    fun build(pos: Int, payload: ByteArray, packId: Int): ByteArray {
        require(payload.isNotEmpty()) { "payload 不能为空" }
        require(payload.size <= 255) { "payload 超过 1 字节长度上限" }
        // 限在 1..254：原 App 的低字节是 `& 0xFF`，回包按 16 位还原；只用单字节
        // 序号时高字节为 0，构建与过滤就是同一个值，不碰 255/256 的边界。
        require(packId in 1..254) { "packid 超出本实现的单字节序号范围" }

        val body = ByteArray(HEADER_LEN + payload.size + PACK_ID_LEN)
        body[0] = STX.toByte()
        body[1] = PROTOCOL_ID.toByte()
        body[2] = (pos shr 8).toByte()
        body[3] = pos.toByte()
        body[4] = 0
        body[5] = payload.size.toByte()
        payload.copyInto(body, HEADER_LEN)
        body[HEADER_LEN + payload.size] = 0
        body[HEADER_LEN + payload.size + 1] = 0
        body[HEADER_LEN + payload.size + 2] = 0
        body[HEADER_LEN + payload.size + 3] = packId.toByte()

        val crc = crc16(body, 1, body.size - 1)
        val frame = body + byteArrayOf((crc shr 8).toByte(), crc.toByte())
        return frame.reversedArray()
    }

    /**
     * 从一次 TCP 读到的字节流中截出所有完整帧（反转流），逐帧反转并解析。
     *
     * 只看帧边界与 CRC；不按 packid 筛选。板子广播的其他客户端帧也会返回，
     * 由客户端按 packid 选。
     */
    fun parseFrames(stream: ByteArray): List<Response> {
        val out = ArrayList<Response>()
        var i = 0
        while (i < stream.size - 1) {
            // 反转帧的结尾固定是 `A5 02`；以它为锚点向前截出一帧。
            val end = i + 1
            val frame = if (isTerminator(stream, i)) frameEndingAt(stream, end) else null
            if (frame == null) {
                i++
            } else {
                out.add(frame)
                i = end + 1
            }
        }
        return out
    }

    private fun isTerminator(stream: ByteArray, index: Int): Boolean =
        (stream[index].toInt() and 0xFF) == RESPONSE_ID &&
            (stream[index + 1].toInt() and 0xFF) == STX

    /**
     * 以反转流里的 `A5 02`（`end` 指向其中的 `02`）为帧尾，截出并解析一帧。
     *
     * 反转帧里：长度低字节在 `end-5`，高字节在 `end-4`（对应正常帧的
     * `normal[5]` / `normal[4]`）。原 App 的高字节写死 0，这里仍按源码的
     * `high * 255 + low` 合成，避免把将来的长帧截错。
     */
    private fun frameEndingAt(stream: ByteArray, end: Int): Response? {
        if (end < 5) return null
        val length = (stream[end - 4].toInt() and 0xFF) * 255 +
            (stream[end - 5].toInt() and 0xFF)
        if (length <= 0) return null
        val start = end + 1 - (12 + length)
        if (start < 0) return null
        val normal = stream.copyOfRange(start, end + 1).reversedArray()
        return parseFrame(normal)
    }

    /**
     * 解析一帧**已反转回正常方向**的帧。
     *
     * 返回 null 表示：头不对、长度不对、CRC 不对、或状态记录不完整。调用方据此
     * 报 `water.frame_invalid`，不猜一个半个结果。
     */
    fun parseFrame(normal: ByteArray): Response? {
        if (normal.size < 12) return null
        if ((normal[0].toInt() and 0xFF) != STX) return null
        if ((normal[1].toInt() and 0xFF) != RESPONSE_ID) return null

        // 原 App 的长度合成是 `high * 255 + low`（不是 256）；高字节固定在 0。
        val length = (normal[4].toInt() and 0xFF) * 255 + (normal[5].toInt() and 0xFF)
        if (length <= 0) return null
        if (normal.size != 12 + length) return null

        val crc = ((normal[normal.size - 2].toInt() and 0xFF) shl 8) or
            (normal[normal.size - 1].toInt() and 0xFF)
        if (crc != crc16(normal, 1, length + 9)) return null

        val pos = ((normal[2].toInt() and 0xFF) shl 8) or (normal[3].toInt() and 0xFF)
        val data = normal.copyOfRange(HEADER_LEN, HEADER_LEN + length)
        // 回包把请求的四个 packid 字节原样带回；按 16 位还原（实测 705 的回包
        // 是 `00 00 02 C1`，302 是 `00 00 01 2E`）。本实现只发 1..254，高字节为 0。
        val packId = ((normal[HEADER_LEN + length + 2].toInt() and 0xFF) shl 8) or
            (normal[HEADER_LEN + length + 3].toInt() and 0xFF)

        // F4 B3：data[1] 固定 0x10，data[2] 门数，data[3] 每条 5 字节。
        if (data.size >= 4 && (data[1].toInt() and 0xFF) == STATUS_MARKER) {
            val n = data[2].toInt() and 0xFF
            val recordLen = data[3].toInt() and 0xFF
            if (recordLen != STATUS_RECORD_LEN || 4 + n * STATUS_RECORD_LEN > data.size) return null
            val doors = ArrayList<DoorStatus>(n)
            for (index in 0 until n) {
                val off = 4 + index * STATUS_RECORD_LEN
                val st = data[off].toInt() and 0xFF
                val bath = (data[off + 1].toInt() and 0xFF) * 255 + (data[off + 2].toInt() and 0xFF)
                val allOpen = (data[off + 3].toInt() and 0xFF) * 255 + (data[off + 4].toInt() and 0xFF)
                doors.add(DoorStatus(index + 1, st, bath, allOpen))
            }
            return Response(packId, pos, Kind.STATUS, data, normal, doors = doors)
        }

        // F4 B1：data[1] = 0xB1，data[2] 门数，data[3] 最长洗浴分钟数。
        if (data.size >= 4 && (data[1].toInt() and 0xFF) == CMD_CONFIG) {
            return Response(
                packId = packId,
                pos = pos,
                kind = Kind.CONFIG,
                data = data,
                raw = normal,
                doorNum = data[2].toInt() and 0xFF,
                bathMinutes = data[3].toInt() and 0xFF,
            )
        }

        // F4 B2：短 ack，data[0] 为返回码。
        return Response(packId, pos, Kind.ACK, data, normal)
    }

    /** CRC-16/CCITT：初值 0、多项式 0x1021、高字节在前。 */
    fun crc16(data: ByteArray, offset: Int, length: Int): Int {
        var crc = 0
        val end = offset + length
        for (i in offset until end) {
            crc = crc xor ((data[i].toInt() and 0xFF) shl 8)
            repeat(8) {
                crc = if (crc and 0x8000 != 0) {
                    ((crc shl 1) xor 0x1021) and 0xFFFF
                } else {
                    (crc shl 1) and 0xFFFF
                }
            }
        }
        return crc and 0xFFFF
    }
}
