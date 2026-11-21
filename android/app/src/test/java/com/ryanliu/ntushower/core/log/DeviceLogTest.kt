package com.ryanliu.ntushower.core.log

import com.ryanliu.ntushower.core.logfile.CappedLogFile
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * 设备统一日志：一行一条 JSON、事件命名、上限裁剪。
 *
 * JSONL 的第一消费者是人（`cat` 取回直接读）与脚本（逐行 json.loads），字段名与
 * 服务端审计同风格（snake_case + kebab-case 事件名），两端对照才不用翻译。
 */
class DeviceLogTest {

    @get:Rule
    val folder = TemporaryFolder()

    private fun readLines(file: File): List<JSONObject> {
        val array = JSONArray()
        file.readLines().filter { it.isNotBlank() }.forEach { array.put(JSONObject(it)) }
        return (0 until array.length()).map { array.getJSONObject(it) }
    }

    @Test
    fun `received and result produce one line each with the shared fields`() {
        val log = DeviceLog(folder.root)

        log.received("cmd-1", "ui.touch")
        log.result("cmd-1", "ok", "")

        val lines = readLines(File(folder.root, DeviceLog.FILE_NAME))
        assertEquals(2, lines.size)
        assertEquals("command.received", lines[0].getString("event"))
        assertEquals("ui.touch", lines[0].getString("action"))
        assertEquals(DeviceLog.SEVERITY_INFO, lines[0].getString("severity"))
        assertEquals("command.result", lines[1].getString("event"))
        assertEquals("cmd-1", lines[1].getString("command_id"))
        // 两类时间都在：ts 给"什么时候"（服务端校准过），uptime_ms 给"开机多久后"
        assertTrue(lines[0].has("ts"))
        assertTrue(lines[0].has("uptime_ms"))
    }

    @Test
    fun `duplicate receipt is marked as its own event`() {
        val log = DeviceLog(folder.root)

        log.received("cmd-1", "(重复接收，重发既有结果)", duplicate = true)

        val lines = readLines(File(folder.root, DeviceLog.FILE_NAME))
        assertEquals("command.duplicate", lines[0].getString("event"))
    }

    @Test
    fun `elapsed_ms only appears when measured`() {
        val log = DeviceLog(folder.root)

        log.result("cmd-1", "ok", "", elapsedMs = 123)
        log.result("cmd-2", "failed", "ui.no_editable_field")

        val lines = readLines(File(folder.root, DeviceLog.FILE_NAME))
        assertEquals(123L, lines[0].getLong("elapsed_ms"))
        assertFalse(lines[1].has("elapsed_ms"))
    }

    @Test
    fun `gateway and lifecycle and error use their own events and severity`() {
        val log = DeviceLog(folder.root)

        log.gateway(connected = true, detail = "trusted=true")
        log.gateway(connected = false, detail = "close")
        log.lifecycle("service.started", "action=watchdog")
        log.warn("notice.window-failed", "addView failed")
        log.error("bridge.open-failed", "adbd not responding")

        val lines = readLines(File(folder.root, DeviceLog.FILE_NAME))
        assertEquals("gateway.connected", lines[0].getString("event"))
        assertEquals(DeviceLog.SEVERITY_INFO, lines[0].getString("severity"))
        assertEquals("gateway.disconnected", lines[1].getString("event"))
        assertEquals("service.started", lines[2].getString("event"))
        assertEquals("action=watchdog", lines[2].getString("detail"))
        assertEquals(DeviceLog.SEVERITY_WARN, lines[3].getString("severity"))
        assertEquals("bridge.open-failed", lines[4].getString("event"))
        assertEquals(DeviceLog.SEVERITY_ERROR, lines[4].getString("severity"))
    }

    @Test
    fun `ts falls back to wall clock without an anchor`() {
        // 无锚点时 ts 回退墙钟（设备刚启动、还没握上手）：日志仍带时刻，锚一立
        // 即回到服务端轴。回退是既定形态，不是第二套时间方案。
        com.ryanliu.ntushower.core.clock.ServerClock.reset()
        val log = DeviceLog(folder.root)

        log.gateway(connected = true, detail = "first line after boot")

        val line = readLines(File(folder.root, DeviceLog.FILE_NAME)).single()
        assertEquals("gateway.connected", line.getString("event"))
        assertTrue(line.getString("ts").endsWith("Z"))
        assertTrue("uptime_ms 不受拨钟影响，恒在", line.getLong("uptime_ms") >= 0)
    }

    @Test
    fun `warn and error advance the critical mark`() {
        val log = DeviceLog(folder.root)
        val before = DeviceLog.criticalMark()

        log.lifecycle("service.started", "action=watchdog")
        assertEquals(before, DeviceLog.criticalMark())
        log.warn("notice.window-failed", "addView failed")
        log.error("bridge.open-failed", "adbd not responding")

        assertEquals(before + 2, DeviceLog.criticalMark())
    }

    @Test
    fun `file is capped at max bytes`() {
        val log = DeviceLog(folder.root, maxBytes = 4096)

        repeat(200) { log.received("cmd-$it", "ui.touch") }

        val file = File(folder.root, DeviceLog.FILE_NAME)
        assertTrue(file.length() <= CappedLogFile.DEFAULT_MAX_BYTES)
        // 裁剪后最后一行仍是完整 JSON：按行硬切会留半条，读的人直接崩
        val last = readLines(file).last()
        assertEquals("command.received", last.getString("event"))
    }
}
