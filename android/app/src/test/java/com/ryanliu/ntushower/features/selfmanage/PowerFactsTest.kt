package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** PowerFacts 的解析与缺失语义：真实参数行格式（device-poweroff.md §7 实测）钉在这里。 */
class PowerFactsTest {

    @get:Rule
    val tmp = TemporaryFolder()

    private fun writeParams(content: String): File {
        val dir = tmp.newFolder("datalook-log")
        val file = File(dir, "Params_2026-11-20.txt")
        file.writeText(content)
        return dir
    }

    @Test
    fun read_parses_the_real_param_line_format() {
        val dir = writeParams(
            "读取参数[autoOpenTime:10:55][autoCloseTime:23:58][closepassword:xxx]\n" +
                "V8下载参数[maxbathtime:1200,...]\n",
        )

        val facts = PowerFacts.read(dir)

        assertTrue(facts != null)
        assertEquals("23:58", facts!!.autoCloseTime)
        assertEquals("10:55", facts.autoOpenTime)
        assertEquals(java.util.TimeZone.getDefault().rawOffset / 60_000, facts.zoneOffsetMinutes)
    }

    @Test
    fun read_takes_the_latest_params_file() {
        val dir = tmp.newFolder("log")
        File(dir, "Params_2026-11-19.txt").writeText("[autoCloseTime:22:00][autoOpenTime:09:00]")
        File(dir, "Params_2026-11-20.txt").writeText("[autoCloseTime:23:58][autoOpenTime:10:55]")

        val facts = PowerFacts.read(dir)

        assertEquals("23:58", facts!!.autoCloseTime)
    }

    @Test
    fun read_returns_null_when_the_directory_is_empty_or_missing() {
        assertNull(PowerFacts.read(tmp.newFolder("empty")))
        assertNull(PowerFacts.read(File(tmp.root, "missing")))
    }

    @Test
    fun read_returns_null_when_the_line_has_no_times() {
        assertNull(PowerFacts.read(writeParams("读取参数[other:1]")))
    }

    @Test
    fun read_returns_null_when_the_params_entry_is_unreadable() {
        // 目录里同名前缀的条目是个子目录：readText 抛异常，按"读不到"如实给 null
        val dir = tmp.newFolder("log")
        File(dir, "Params_2026-11-20.txt").mkdirs()

        assertNull(PowerFacts.read(dir))
    }

    @Test
    fun read_has_a_no_argument_entry_point_for_production_call_sites() {
        // 生产调用方（AgentService/TimeSync）走无参重载，读 AppConfig.BATH_PARAMS_DIR。
        // 真机路径在 JVM 上不存在 → null；这条钉住"默认参数与 AppConfig 常量绑定"不漂移。
        assertNull(PowerFacts.read())
    }

    @Test
    fun payload_carries_the_three_reported_fields() {
        val payload = PowerFacts("23:58", "10:55", 480).toPayload()

        assertEquals("23:58", payload["auto_close_time"])
        assertEquals("10:55", payload["auto_open_time"])
        assertEquals(480, payload["zone_offset_minutes"])
    }

    @Test
    fun fingerprint_changes_when_any_fact_changes() {
        val base = PowerFacts("23:58", "10:55", 480)

        assertEquals(base.fingerprint(), base.fingerprint())
        assertTrue(base.copy(autoCloseTime = "23:59").fingerprint() != base.fingerprint())
        assertTrue(base.copy(autoOpenTime = "10:56").fingerprint() != base.fingerprint())
        assertTrue(base.copy(zoneOffsetMinutes = 0).fingerprint() != base.fingerprint())
    }
}
