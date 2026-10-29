package com.ryanliu.ntushower.features.water

import com.ryanliu.ntushower.features.automation.CommandScheduler
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `water.open/close/status` 的结果组装与失败映射。
 *
 * 不碰真实板子：映射与四条命令都由测试注入；协议字节另有 [WaterFrameTest]，
 * TCP 链路另有 [WaterBoardClientTest]。
 */
class WaterHandlerTest {

    private val board = WaterTargetResolver.Board(
        code = "0025",
        name = "啬园校区28号楼1层",
        ip = "172.21.30.50",
        port = 4001,
        pos = 1,
        start = 1,
        count = 7,
    )

    private val target = WaterTargetResolver.Target(board, doorNo = 3, boardDoor = 3)

    @Test
    fun `open reports the resolved board and board door`() {
        val fake = FakeBoard()
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterOpen("c-open", 3),
            resolveTarget = { if (it == 3) target else null },
            board = fake,
        )

        assertEquals(OutcomeStatus.OK, outcome.status)
        assertEquals("", outcome.reasonCode)
        assertTrue(outcome.terminalMessage.contains("已登记占用"))
        assertTrue(fake.openCalled)
        assertFalse(fake.closeCalled)
        val data = requireNotNull(outcome.data)
        assertEquals(3, data.getInt("door_no"))
        assertEquals(3, data.getInt("board_door"))
        assertTrue(data.getBoolean("ack"))
        assertEquals("00", data.getString("raw"))
        val boardJson = data.getJSONObject("board")
        assertEquals("0025", boardJson.getString("code"))
        assertEquals(1, boardJson.getInt("start"))
        assertEquals(7, boardJson.getInt("count"))
    }

    @Test
    fun `close uses the close command`() {
        val fake = FakeBoard()
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterClose("c-close", 3),
            resolveTarget = { target },
            board = fake,
        )

        assertEquals(OutcomeStatus.OK, outcome.status)
        assertTrue(outcome.terminalMessage.contains("已关阀"))
        assertTrue(fake.closeCalled)
        assertFalse(fake.openCalled)
    }

    @Test
    fun `default board is not touched when the mapping fails`() {
        // 不传 board：验证生产默认参数只在调用时求值，且映射失败不会发起网络请求。
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterOpen("c-default", 99),
            resolveTarget = { null },
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_TARGET_NOT_FOUND, outcome.reasonCode)
    }

    @Test
    fun `close rejection uses the close wording`() {
        val fake = FakeBoard(switchResponse = ack(returnCode = 1))
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterClose("c-close-rejected", 3),
            resolveTarget = { target },
            board = fake,
        )

        assertEquals(ReasonCodes.WATER_BOARD_REJECTED, outcome.reasonCode)
        assertTrue(outcome.terminalMessage.contains("关阀"))
    }

    @Test
    fun `open transport failure keeps the opening wording and reason`() {
        val fake = FakeBoard(failure = WaterBoardException(ReasonCodes.WATER_BOARD_UNREACHABLE, "板子不可达"))
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterOpen("c-open-fail", 3),
            resolveTarget = { target },
            board = fake,
        )

        assertEquals(ReasonCodes.WATER_BOARD_UNREACHABLE, outcome.reasonCode)
        assertEquals("板子不可达", outcome.terminalMessage)
    }

    @Test
    fun `missing mapping fails before touching the board`() {
        val fake = FakeBoard()
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterOpen("c-missing", 99),
            resolveTarget = { null },
            board = fake,
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_TARGET_NOT_FOUND, outcome.reasonCode)
        assertFalse(fake.openCalled)
    }

    @Test
    fun `a rejected switch keeps the board target and raw reply`() {
        val fake = FakeBoard(switchResponse = ack(returnCode = 1))
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterOpen("c-rejected", 3),
            resolveTarget = { target },
            board = fake,
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_BOARD_REJECTED, outcome.reasonCode)
        assertTrue(outcome.terminalMessage.contains("return=1"))
        val data = requireNotNull(outcome.data)
        assertEquals(3, data.getInt("board_door"))
        assertEquals("01", data.getString("raw"))
    }

    @Test
    fun `a transport failure keeps its reason code`() {
        val fake = FakeBoard(failure = WaterBoardException(ReasonCodes.WATER_TIMEOUT, "板子超时"))
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterClose("c-timeout", 3),
            resolveTarget = { target },
            board = fake,
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_TIMEOUT, outcome.reasonCode)
        assertEquals("板子超时", outcome.terminalMessage)
    }

    @Test
    fun `status returns only the waterList doors and the board config`() {
        val fake = FakeBoard(
            configResponse = config(doorNum = 24, bathMin = 60),
            statusResponse = status(
                doors = listOf(
                    door(1, 0x07, 194, 43),
                    door(2, 0x01, 2250, 92),
                    door(3, 0x00, 0, 0),
                ),
            ),
        )
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-status"),
            listBoards = { listOf(board.copy(count = 2)) },
            board = fake,
        )

        assertEquals(OutcomeStatus.OK, outcome.status)
        val rows = requireNotNull(outcome.data).getJSONArray("boards")
        assertEquals(1, rows.length())
        val row = rows.getJSONObject(0)
        assertEquals(24, row.getJSONObject("config").getInt("door_num"))
        assertEquals(60, row.getJSONObject("config").getInt("bath_min"))
        val doors = row.getJSONArray("doors")
        assertEquals(2, doors.length())
        assertEquals(1, doors.getJSONObject(0).getInt("door_no"))
        assertEquals(0x07, doors.getJSONObject(0).getInt("status"))
        assertEquals(194, doors.getJSONObject(0).getInt("bath_s"))
        assertEquals(43, doors.getJSONObject(0).getInt("allopen_s"))
        assertEquals(2, doors.getJSONObject(1).getInt("door_no"))
        assertEquals("00B1183C", row.getJSONObject("raw").getString("config"))
        assertTrue(row.getJSONObject("raw").getString("status").isNotEmpty())
    }

    @Test
    fun `status without any board is a mapping failure`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-empty"),
            listBoards = { emptyList() },
            board = FakeBoard(),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_TARGET_NOT_FOUND, outcome.reasonCode)
    }

    @Test
    fun `status rejects a config read the board refused`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-config-refused"),
            listBoards = { listOf(board) },
            board = FakeBoard(configResponse = config(returnCode = 2, doorNum = 7, bathMin = 20)),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_BOARD_REJECTED, outcome.reasonCode)
        assertTrue(outcome.terminalMessage.contains("return=2"))
    }

    @Test
    fun `status rejects a config reply without door count or minutes`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-config-empty"),
            listBoards = { listOf(board) },
            board = FakeBoard(
                configResponse = WaterFrame.Response(
                    packId = 1,
                    pos = 1,
                    kind = WaterFrame.Kind.CONFIG,
                    data = byteArrayOf(0, 0xB1.toByte()),
                    raw = byteArrayOf(0, 0xB1.toByte()),
                ),
            ),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_FRAME_INVALID, outcome.reasonCode)
    }

    @Test
    fun `status rejects a status read the board refused`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-status-refused"),
            listBoards = { listOf(board) },
            board = FakeBoard(statusResponse = status(returnCode = 3, doors = emptyList())),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_BOARD_REJECTED, outcome.reasonCode)
    }

    @Test
    fun `status rejects a reply with fewer doors than the waterList needs`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-short"),
            listBoards = { listOf(board.copy(count = 7)) },
            board = FakeBoard(statusResponse = status(doors = listOf(door(1, 0, 0, 0)))),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_FRAME_INVALID, outcome.reasonCode)
        assertTrue(outcome.terminalMessage.contains("1 门"))
    }

    @Test
    fun `status maps a transport failure to its reason code`() {
        val outcome = WaterHandler.execute(
            CommandScheduler.Command.WaterStatus("c-status-timeout"),
            listBoards = { listOf(board) },
            board = FakeBoard(failure = WaterBoardException(ReasonCodes.WATER_BOARD_UNREACHABLE, "板子不可达")),
        )

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.WATER_BOARD_UNREACHABLE, outcome.reasonCode)
    }

    @Test
    fun `a non water command is rejected without touching the board`() {
        val fake = FakeBoard()
        val outcome = WaterHandler.execute(CommandScheduler.Command.Photo("c-photo"), board = fake)

        assertEquals(OutcomeStatus.FAILED, outcome.status)
        assertEquals(ReasonCodes.PROTO_UNKNOWN_ACTION, outcome.reasonCode)
        assertFalse(fake.openCalled)
        assertFalse(fake.closeCalled)
    }

    private class FakeBoard(
        private val switchResponse: WaterFrame.Response = ack(),
        private val configResponse: WaterFrame.Response = config(),
        private val statusResponse: WaterFrame.Response = status(),
        private val failure: WaterBoardException? = null,
    ) : WaterBoard {
        var openCalled = false
        var closeCalled = false

        override fun open(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response {
            openCalled = true
            failure?.let { throw it }
            return switchResponse
        }

        override fun close(board: WaterTargetResolver.Board, boardDoor: Int): WaterFrame.Response {
            closeCalled = true
            failure?.let { throw it }
            return switchResponse
        }

        override fun config(board: WaterTargetResolver.Board): WaterFrame.Response {
            failure?.let { throw it }
            return configResponse
        }

        override fun status(board: WaterTargetResolver.Board): WaterFrame.Response {
            failure?.let { throw it }
            return statusResponse
        }
    }

    private companion object {
        fun ack(returnCode: Int = 0): WaterFrame.Response = WaterFrame.Response(
            packId = 1,
            pos = 1,
            kind = WaterFrame.Kind.ACK,
            data = byteArrayOf(returnCode.toByte()),
            raw = byteArrayOf(returnCode.toByte()),
        )

        fun config(returnCode: Int = 0, doorNum: Int = 7, bathMin: Int = 20) = WaterFrame.Response(
            packId = 1,
            pos = 1,
            kind = WaterFrame.Kind.CONFIG,
            data = byteArrayOf(returnCode.toByte(), 0xB1.toByte(), doorNum.toByte(), bathMin.toByte()),
            raw = byteArrayOf(returnCode.toByte(), 0xB1.toByte(), doorNum.toByte(), bathMin.toByte()),
            doorNum = doorNum,
            bathMinutes = bathMin,
        )

        fun status(returnCode: Int = 0, doors: List<WaterFrame.DoorStatus> = emptyList()): WaterFrame.Response =
            WaterFrame.Response(
                packId = 1,
                pos = 1,
                kind = WaterFrame.Kind.STATUS,
                data = byteArrayOf(returnCode.toByte(), 0x10, doors.size.toByte(), 5),
                raw = byteArrayOf(returnCode.toByte(), 0x10, doors.size.toByte(), 5),
                doors = doors,
            )

        fun door(boardDoor: Int, status: Int, bath: Int, allOpen: Int) =
            WaterFrame.DoorStatus(boardDoor, status, bath, allOpen)
    }
}
