package com.ryanliu.ntushower.features.water

import com.ryanliu.ntushower.core.shell.ShellChannel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `data.xml` 的 waterList 解析回归。
 *
 * 样本来自六台设备（1F .50 / 2F .52 / 3F .54 / 4F .56 / 5F .58 / 6F .60）：
 * 六台的 waterList 都是 start=1、nozzlenumber=7；6F 的板子自身是 24 门板，
 * 但 waterList 仍是 7，控制/状态只取 7。`BathControlPosno` 六台都是 0001。
 */
class WaterTargetResolverTest {

    private val oneFloor = """
        <?xml version='1.0' encoding='utf-8' standalone='yes' ?>
        <map>
            <string name="waterList">[{&quot;nozzlenumber&quot;:&quot;7&quot;,&quot;startnumber&quot;:&quot;1&quot;,&quot;watercode&quot;:&quot;0025&quot;,&quot;waterport&quot;:&quot;4001&quot;,&quot;waterip&quot;:&quot;172.21.30.50&quot;,&quot;watername&quot;:&quot;啬园校区28号楼1层&quot;}]</string>
            <string name="BathControlPosno">0001</string>
        </map>
    """.trimIndent()

    private val sixFloor = """
        <?xml version='1.0' encoding='utf-8' standalone='yes' ?>
        <map>
            <string name="waterList">[{&quot;nozzlenumber&quot;:&quot;7&quot;,&quot;startnumber&quot;:&quot;1&quot;,&quot;watercode&quot;:&quot;0030&quot;,&quot;waterport&quot;:&quot;4001&quot;,&quot;waterip&quot;:&quot;172.21.30.60&quot;,&quot;watername&quot;:&quot;啬园校区28号楼6层&quot;}]</string>
            <string name="BathControlPosno">0001</string>
        </map>
    """.trimIndent()

    @Test
    fun `parses board and maps logical door to board door`() {
        val boards = WaterTargetResolver.parse(oneFloor)
        assertEquals(1, boards.size)
        val board = boards.single()
        assertEquals("0025", board.code)
        assertEquals("172.21.30.50", board.ip)
        assertEquals(4001, board.port)
        assertEquals(1, board.pos)
        assertEquals(1, board.start)
        assertEquals(7, board.count)

        val first = WaterTargetResolver.targetIn(boards, 1)
        assertNotNull(first)
        assertEquals(1, first!!.boardDoor)

        val last = WaterTargetResolver.targetIn(boards, 7)
        assertEquals(7, last!!.boardDoor)
        assertNull(WaterTargetResolver.targetIn(boards, 8))

        // contains 是 targetIn 的判定本体：起点前一位与终点后一位都在窗外
        assertTrue(board.contains(1))
        assertTrue(board.contains(7))
        assertTrue(!board.contains(0))
        assertTrue(!board.contains(8))
    }

    @Test
    fun `6F waterList stays seven even though the board reports 24`() {
        val boards = WaterTargetResolver.parse(sixFloor)
        assertEquals(1, boards.size)
        assertEquals("172.21.30.60", boards.single().ip)
        assertEquals(7, boards.single().count)
        assertNotNull(WaterTargetResolver.targetIn(boards, 7))
        assertNull(WaterTargetResolver.targetIn(boards, 8))
    }

    @Test
    fun `supports a non one start and maps the range`() {
        val xml = oneFloor.replace(
            "&quot;startnumber&quot;:&quot;1&quot;",
            "&quot;startnumber&quot;:&quot;8&quot;",
        )
        val boards = WaterTargetResolver.parse(xml)
        assertEquals(8, boards.single().start)
        assertEquals(1, WaterTargetResolver.targetIn(boards, 8)!!.boardDoor)
        assertEquals(7, WaterTargetResolver.targetIn(boards, 14)!!.boardDoor)
        assertNull(WaterTargetResolver.targetIn(boards, 15))
    }

    @Test
    fun `skips records whose required fields are missing or out of range`() {
        val list = """
            [
              {"waterip":"","waterport":"4001","startnumber":"1","nozzlenumber":"7"},
              {"waterip":"172.21.30.50","waterport":"abc","startnumber":"1","nozzlenumber":"7"},
              {"waterip":"172.21.30.50","waterport":"70000","startnumber":"1","nozzlenumber":"7"},
              {"waterip":"172.21.30.50","waterport":"4001","startnumber":"abc","nozzlenumber":"7"},
              {"waterip":"172.21.30.50","waterport":"4001","startnumber":"0","nozzlenumber":"7"},
              {"waterip":"172.21.30.50","waterport":"4001","startnumber":"1","nozzlenumber":"abc"},
              {"waterip":"172.21.30.50","waterport":"4001","startnumber":"1","nozzlenumber":"0"},
              {"waterip":"172.21.30.50","waterport":"4001","startnumber":"1","nozzlenumber":"7","watercode":"0025","watername":"1层"}
            ]
        """.trimIndent()
        val boards = WaterTargetResolver.parse(xml(list))
        assertEquals(1, boards.size)
        assertEquals("0025", boards.single().code)
        assertEquals(7, boards.single().count)
    }

    @Test
    fun `malformed XML or waterList yields no boards`() {
        assertTrue(WaterTargetResolver.parse("<map>").isEmpty())
        assertTrue(WaterTargetResolver.parse(xml("not json")).isEmpty())
        assertTrue(WaterTargetResolver.parse(xml("[1, 2]")).isEmpty())
    }

    @Test
    fun `blank waterList yields no boards`() {
        assertTrue(WaterTargetResolver.parse(xml("   ")).isEmpty())
    }

    private fun xml(waterList: String, pos: String = "1"): String = listOf(
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>",
        "<map>",
        "<string name=\"waterList\">$waterList</string>",
        "<string name=\"BathControlPosno\">$pos</string>",
        "</map>",
    ).joinToString(10.toChar().toString())

    @Test
    fun `boards reads through the injected shell`() {
        val boards = WaterTargetReader.boards { ShellChannel.Result(oneFloor, 0) }
        assertEquals(1, boards.size)
        assertEquals("172.21.30.50", boards.single().ip)

        val target = WaterTargetReader.target(1) { ShellChannel.Result(oneFloor, 0) }
        assertEquals(1, target!!.boardDoor)
    }

    @Test
    fun `boards rejects a failed or blank shell result`() {
        assertTrue(WaterTargetReader.boards { ShellChannel.Result(oneFloor, 1) }.isEmpty())
        assertTrue(WaterTargetReader.boards { ShellChannel.Result("   ", 0) }.isEmpty())
    }

    @Test
    fun `empty or missing waterList yields no boards and no default board`() {
        assertTrue(WaterTargetResolver.parse("").isEmpty())
        assertTrue(WaterTargetResolver.parse("<map></map>").isEmpty())
        assertTrue(
            WaterTargetResolver.parse(
                "<map><string name=\"BathControlPosno\">0001</string></map>",
            ).isEmpty(),
        )
        assertTrue(
            WaterTargetResolver.parse(
                "<map><string name=\"waterList\">[]</string><string name=\"BathControlPosno\">0001</string></map>",
            ).isEmpty(),
        )
    }
}
