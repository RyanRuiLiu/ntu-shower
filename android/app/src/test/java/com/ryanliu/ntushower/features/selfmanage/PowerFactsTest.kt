package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * PowerFacts 的解析与缺失语义：真实参数行格式（device-poweroff.md §7 实测）钉在这里。
 * ls/read 读取函数注入为纯函数（生产默认绑定 RootShell），用完整路径的内存"文件"
 * 模拟目录列举与读取，不依赖真机文件系统。
 */
class PowerFactsTest {

    private val dir = "/sdcard/datalook/log"
    private val files = mutableMapOf<String, String>()

    private fun writeParams(name: String, content: String) {
        files["$dir/$name"] = content
    }

    private fun read(): PowerFacts? = PowerFacts.read(
        dir,
        ls = { files.keys.map { it.substringAfterLast("/") }.sorted() },
        readFile = { path -> files[path] },
    )

    @Test
    fun read_parses_the_real_param_line_format() {
        writeParams(
            "Params_2026-11-20.txt",
            "读取参数[autoOpenTime:10:55][autoCloseTime:23:58][closepassword:xxx]\n" +
                "V8下载参数[maxbathtime:1200,...]\n",
        )

        val facts = read()

        assertTrue(facts != null)
        assertEquals("23:58", facts!!.autoCloseTime)
        assertEquals("10:55", facts.autoOpenTime)
        assertEquals(java.util.TimeZone.getDefault().rawOffset / 60_000, facts.zoneOffsetMinutes)
    }

    @Test
    fun read_takes_the_latest_params_file() {
        writeParams("Params_20261119.txt", "[autoCloseTime:22:00][autoOpenTime:09:00]")
        writeParams("Params_20261120.txt", "[autoCloseTime:23:58][autoOpenTime:10:55]")

        val facts = read()

        assertEquals("23:58", facts!!.autoCloseTime)
    }

    @Test
    fun read_returns_null_when_the_directory_is_empty() {
        assertNull(read())
    }

    @Test
    fun read_returns_null_when_the_line_has_no_times() {
        writeParams("Params_20261120.txt", "读取参数[other:1]")

        assertNull(read())
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
