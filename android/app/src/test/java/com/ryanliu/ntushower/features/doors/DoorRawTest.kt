package com.ryanliu.ntushower.features.doors

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DoorRawTest {

    private val validLine = "1|0|2530110052|1010209501|20260930003029|||0|0"

    // ── parseRows / parseRow ──

    @Test
    fun parseRows_validLine() {
        val rows = DoorRaw.parseRows(validLine)

        assertEquals(1, rows.size)
        val d = rows[0]
        assertEquals(1, d.doorNo)
        assertEquals(0L, d.cardno)
        assertEquals("2530110052", d.certno)
        assertEquals("1010209501", d.accno)
        assertEquals("20260930003029", d.openTime)
    }

    @Test
    fun parseRows_malformedReturnsEmpty() {
        assertEquals(0, DoorRaw.parseRows("bad line").size)
        assertEquals(0, DoorRaw.parseRows("").size)
    }

    @Test
    fun parseRows_skipsBlankLinesButKeepsValidOnes() {
        val rows = DoorRaw.parseRows("\n$validLine\n\n")

        assertEquals(1, rows.size)
    }

    @Test
    fun parseRow_rejectsTooFewColumns() {
        // 列数不足说明 sqlite3 输出被截断或表结构不符
        assertNull(DoorRaw.parseRow("1|0|2530110052"))
    }

    @Test
    fun parseRow_rejectsNonNumericDoorNo() {
        assertNull(DoorRaw.parseRow("x|0|c|a|t|||0|0"))
    }

    @Test
    fun parseRow_rejectsNonNumericStatus() {
        assertNull(DoorRaw.parseRow("1|0|c|a|t|||0|x"))
    }

    @Test
    fun parseRow_fallsBackToZeroForOptionalNumbers() {
        // 卡号与时长缺失不应导致整行丢弃：喷头编号与状态才是索引依据
        val row = DoorRaw.parseRow("2|x|c|a|t|||y|1")

        assertEquals(2, row?.doorNo)
        assertEquals(0L, row?.cardno)
        assertEquals(0, row?.bathTime)
        assertEquals(1, row?.status)
    }

    @Test
    fun parseRow_trimsNumericFields() {
        val row = DoorRaw.parseRow(" 3 | 7 | c | a | t | | | 5 | 2 ")

        assertEquals(3, row?.doorNo)
        assertEquals(7L, row?.cardno)
        assertEquals(2, row?.status)
    }

    @Test
    fun parseRow_keepsEmptyTimeFieldsAsEmpty() {
        val row = DoorRaw.parseRow("1|0|c|a||||0|0")

        assertEquals("", row?.openTime)
        assertEquals("", row?.inTime)
    }

    // ── toPayload ──

    @Test
    fun toPayload_convertsLocalTimeToUtc() {
        val payload = DoorRaw.parseRows(validLine)[0].toPayload()

        assertEquals(1, payload.getInt("door_no"))
        assertEquals("2530110052", payload.getString("cert_no"))
        assertNull(payload.opt("in_time")) // 空时间不上报
        assertNull(payload.opt("close_time"))
        assertEquals(0, payload.getInt("bath_time_s"))
        assertEquals(0, payload.getInt("status_raw"))
    }

    @Test
    fun toPayload_nonEmptyTimeBecomesRfc3339() {
        val payload = DoorRaw.parseRows(validLine)[0].toPayload()

        assertTrue(payload.getString("open_time").endsWith("Z"))
    }

    @Test
    fun toPayload_carriesIdentityFields() {
        val payload = DoorRaw.parseRows(validLine)[0].toPayload()

        assertEquals(0L, payload.getLong("card_no"))
        assertEquals("1010209501", payload.getString("acc_no"))
    }

    @Test
    fun toPayload_omitsUnparsableTime() {
        // 脏时间宁可不上报，也不要给出一个猜测的时间戳
        val row = DoorRaw.parseRow("1|0|c|a|not-a-time|||0|0")

        assertNull(row?.toPayload()?.opt("open_time"))
    }

    @Test
    fun toPayload_omitsBlankTime() {
        val row = DoorRaw.parseRow("1|0|c|a|   |||0|0")

        assertNull(row?.toPayload()?.opt("open_time"))
    }

    @Test
    fun toPayload_reportsAllProvidedTimes() {
        val line = "1|0|c|a|20260930003029|20260930003100|20260930004000|600|1"
        val payload = DoorRaw.parseRows(line)[0].toPayload()

        assertTrue(payload.has("open_time"))
        assertTrue(payload.has("in_time"))
        assertTrue(payload.has("close_time"))
        assertEquals(600, payload.getInt("bath_time_s"))
        assertEquals(1, payload.getInt("status_raw"))
    }

    @Test
    fun exposesAllColumnsFromTheTable() {
        // 字段与 t_doorlist 列一一对应：逐一读取以确保映射未走样
        val row = DoorRaw.parseRow("1|9|c|a|20260930003029|20260930003100|20260930004000|600|1")

        assertEquals(1, row?.doorNo)
        assertEquals(9L, row?.cardno)
        assertEquals("c", row?.certno)
        assertEquals("a", row?.accno)
        assertEquals("20260930003029", row?.openTime)
        assertEquals("20260930003100", row?.inTime)
        assertEquals("20260930004000", row?.closeTime)
        assertEquals(600, row?.bathTime)
        assertEquals(1, row?.status)
    }

    @Test
    fun toPayload_isAPlainJsonObject() {
        // 上报前会被序列化进信封，字段类型必须是 JSON 原生类型
        val payload: JSONObject = DoorRaw.parseRows(validLine)[0].toPayload()

        assertTrue(payload.keys().asSequence().toList().isNotEmpty())
    }

    // ── fingerprint ──

    @Test
    fun fingerprint_ignoresBathTime() {
        // 只有 bathTime 不同的两行，指纹必须相同。它是累计值，不参与状态派生：服务端
        // 判断"暂停"只看它 > 0，网页端显示的时长又是拿 inTime 与当前时刻自己算的。
        // 放进指纹会让这种"内容与语义都相同"的两帧被判成变化。
        val a = DoorRaw.parseRow("1|0|c|a|20260930003029|20260930003100|20260930004000|10|1")
        val b = DoorRaw.parseRow("1|0|c|a|20260930003029|20260930003100|20260930004000|11|1")

        assertEquals(a?.fingerprint(), b?.fingerprint())
    }

    @Test
    fun fingerprint_seesEveryStateTransition() {
        // 真正值得上报的那几件事必须被指纹看见，否则会漏报（比多报严重得多）
        val fingerprints = listOf(
            "1|0|c|a||||0|0", // 空闲
            "1|1001|c|a|20260930003029|||0|0", // 有人进来
            "1|1001|c|a|20260930003029|20260930003100||30|1", // 阀开了
            "1|1001|c|a|20260930003029|20260930003100|20260930004000|600|1", // 阀关了
        ).map { DoorRaw.parseRow(it)?.fingerprint() }

        assertEquals(4, fingerprints.distinct().size)
    }

    // ── isOccupied ──

    @Test
    fun isOccupied_cardNoCounts() {
        assertTrue(DoorRaw.parseRow("1|1001|c|a||||0|0")!!.isOccupied())
    }

    @Test
    fun isOccupied_accNoCountsUnlessBlankOrZero() {
        assertTrue(DoorRaw.parseRow("1|0|c|a||||0|0")!!.isOccupied())
        assertEquals(false, DoorRaw.parseRow("1|0|2530110052|0||||0|0")!!.isOccupied())
        assertEquals(false, DoorRaw.parseRow("1|0|2530110052|||||0|0")!!.isOccupied())
    }

    @Test
    fun isOccupied_repairAndDisabledAreNeverOccupied() {
        assertEquals(false, DoorRaw.parseRow("1|1001|c|a||||0|1")!!.isOccupied())
        assertEquals(false, DoorRaw.parseRow("1|1001|c|a||||0|3")!!.isOccupied())
    }

    @Test
    fun isOccupied_selectedWithoutOccupantFieldsDoesNotCount() {
        assertEquals(false, DoorRaw.parseRow("1|0|c||||||0|2")!!.isOccupied())
        // 已选中但同时有占用字段时，按服务端判据仍算占用
        assertTrue(DoorRaw.parseRow("1|0|c|a||||0|2")!!.isOccupied())
    }

    // ── parseReading ──

    @Test
    fun parseReading_completeWhenEveryLineParses() {
        val reading = DoorRaw.parseReading("\n$validLine\n", 0)

        assertTrue(reading.complete)
        assertEquals(1, reading.doors.size)
    }

    @Test
    fun parseReading_incompleteOnFailureEmptyOrBadLine() {
        assertEquals(false, DoorRaw.parseReading(validLine, 1).complete)
        assertEquals(false, DoorRaw.parseReading("", 0).complete)
        assertEquals(false, DoorRaw.parseReading("\n\n", 0).complete)

        val partial = DoorRaw.parseReading("$validLine\nbad line\n", 0)
        assertEquals(false, partial.complete)
        assertEquals(1, partial.doors.size)
    }
}
