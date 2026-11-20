package com.ryanliu.ntushower.features.selfmanage

import com.ryanliu.ntushower.core.config.AppConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * PowerFacts 的解析与缺失语义：真实 `data.xml` 片段按反编译源码的键名钉在这里
 * （`WelcomeActivity:564` 读 `autoopentime` / `autoclosetime`）。读取函数注入为纯
 * 函数（生产默认绑定 RootShell），不依赖真机文件系统。
 */
class PowerFactsTest {

    private val xml = """
        <?xml version='1.0' encoding='utf-8' standalone='yes' ?>
        <map>
            <string name="roomname">管庄男浴室</string>
            <string name="autoopentime">10:55</string>
            <string name="autoclosetime">23:58</string>
            <int name="posserverport" value="5500" />
        </map>
    """.trimIndent()

    private fun read(text: String?): PowerFacts? = PowerFacts.read(readFile = { text })

    @Test
    fun read_parses_the_app_shared_preferences() {
        val facts = read(xml)

        assertTrue(facts != null)
        assertEquals("23:58", facts!!.autoCloseTime)
        assertEquals("10:55", facts.autoOpenTime)
        assertEquals(java.util.TimeZone.getDefault().rawOffset / 60_000, facts.zoneOffsetMinutes)
    }

    @Test
    fun read_returns_null_when_the_file_is_missing() {
        assertNull(read(null))
    }

    @Test
    fun read_returns_null_when_either_key_is_absent() {
        assertNull(read("""<map><string name="autoopentime">10:55</string></map>"""))
        assertNull(read("""<map><string name="autoclosetime">23:58</string></map>"""))
    }

    @Test
    fun read_command_cats_the_prefs_path_instead_of_executing_it() {
        assertEquals(
            "cat '${AppConfig.BATH_PREFS_PATH}'",
            PowerFacts.readCommand(AppConfig.BATH_PREFS_PATH),
        )
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
