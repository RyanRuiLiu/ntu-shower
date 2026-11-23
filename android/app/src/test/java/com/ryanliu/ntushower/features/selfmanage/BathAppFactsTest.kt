package com.ryanliu.ntushower.features.selfmanage

import com.ryanliu.ntushower.core.config.AppConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * BathAppFacts 的解析与缺失语义：真实 `data.xml` 片段按反编译源码的键名钉在这里
 * （`WelcomeActivity:564` 读 `autoopentime` / `autoclosetime`；阈值三键为属性形态，
 * 2026-11-23 现场实测）。读取函数注入为纯函数（生产默认绑定 RootShell），不依赖
 * 真机文件系统。
 */
class BathAppFactsTest {

    private val xml = """
        <?xml version='1.0' encoding='utf-8' standalone='yes' ?>
        <map>
            <string name="roomname">管庄男浴室</string>
            <string name="autoopentime">10:55</string>
            <string name="autoclosetime">23:58</string>
            <int name="maxbatchtime" value="20" />
            <int name="maxinspacetime" value="5" />
            <int name="maxbatchspacetime" value="5" />
            <int name="posserverport" value="5500" />
        </map>
    """.trimIndent()

    private fun read(text: String?): BathAppFacts? = BathAppFacts.read(readFile = { text })

    @Test
    fun read_parses_the_app_shared_preferences() {
        val facts = read(xml)

        assertTrue(facts != null)
        assertEquals("23:58", facts!!.autoCloseTime)
        assertEquals("10:55", facts.autoOpenTime)
        assertEquals(java.util.TimeZone.getDefault().rawOffset / 60_000, facts.zoneOffsetMinutes)
        assertEquals(20, facts.maxBatchTimeMin)
        assertEquals(5, facts.maxInSpaceTimeMin)
        assertEquals(5, facts.maxBatchSpaceTimeMin)
    }

    @Test
    fun read_returns_null_when_the_file_is_missing() {
        assertNull(read(null))
    }

    @Test
    fun read_returns_null_when_a_required_key_is_absent() {
        assertNull(read("""<map><string name="autoopentime">10:55</string></map>"""))
        assertNull(read("""<map><string name="autoclosetime">23:58</string></map>"""))
        assertNull(read("""<map><int name="maxbatchtime" value="20" /></map>"""))
        assertNull(read("""<map><int name="maxinspacetime" value="5" /></map>"""))
        assertNull(read("""<map><int name="maxbatchspacetime" value="5" /></map>"""))
    }

    @Test
    fun read_command_cats_the_prefs_path_instead_of_executing_it() {
        assertEquals(
            "cat '${AppConfig.BATH_PREFS_PATH}'",
            BathAppFacts.readCommand(AppConfig.BATH_PREFS_PATH),
        )
    }

    @Test
    fun payload_carries_the_six_reported_fields() {
        val payload = BathAppFacts("23:58", "10:55", 480, 20, 5, 5).toPayload()

        assertEquals("23:58", payload["auto_close_time"])
        assertEquals("10:55", payload["auto_open_time"])
        assertEquals(480, payload["zone_offset_minutes"])
        assertEquals(20, payload["max_batch_time_min"])
        assertEquals(5, payload["max_in_space_time_min"])
        assertEquals(5, payload["max_batch_space_time_min"])
    }

    @Test
    fun fingerprint_changes_when_any_fact_changes() {
        val base = BathAppFacts("23:58", "10:55", 480, 20, 5, 5)

        assertEquals(base.fingerprint(), base.fingerprint())
        assertTrue(base.copy(autoCloseTime = "23:59").fingerprint() != base.fingerprint())
        assertTrue(base.copy(autoOpenTime = "10:56").fingerprint() != base.fingerprint())
        assertTrue(base.copy(zoneOffsetMinutes = 0).fingerprint() != base.fingerprint())
        assertTrue(base.copy(maxBatchTimeMin = 30).fingerprint() != base.fingerprint())
        assertTrue(base.copy(maxInSpaceTimeMin = 6).fingerprint() != base.fingerprint())
        assertTrue(base.copy(maxBatchSpaceTimeMin = 6).fingerprint() != base.fingerprint())
    }
}
