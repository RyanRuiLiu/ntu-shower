package com.ryanliu.ntushower.features.statusline

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StatusLineTest {

    private fun floor(name: String, free: Int?, online: Boolean = true): FloorStatus =
        FloorStatus(deviceId = "dev-$name", name = name, online = online, free = free)

    @Test
    fun `text lists every floor in payload order with free counts`() {
        val floors = listOf(
            floor("1F", 3),
            floor("2F", 7),
            floor("3F", 0),
            floor("4F", null, online = false),
            floor("5F", 1),
            floor("6F", 2),
        )

        assertEquals("各楼层空闲 1F 3 · 2F 7 · 3F 0 · 4F 离线 · 5F 1 · 6F 2", StatusLine.text(floors))
    }

    @Test
    fun `zero free is a number while offline and no data are distinct words`() {
        val parts = StatusLine.parts(
            listOf(floor("1F", 0), floor("2F", null, online = false), floor("3F", null, online = true)),
        )

        val entries = parts.filter { it.tone != StatusTone.PLAIN }.map { it.text }
        assertEquals(listOf("1F 0", "2F 离线", "3F 无数据"), entries)
        assertEquals(
            listOf(StatusTone.EMPTY, StatusTone.UNAVAILABLE, StatusTone.UNAVAILABLE),
            parts.filter { it.tone != StatusTone.PLAIN }.map { it.tone },
        )
    }

    @Test
    fun `positive free is the only available tone`() {
        val parts = StatusLine.parts(listOf(floor("1F", 2), floor("2F", 0), floor("3F", 1)))
        assertEquals(
            listOf(StatusTone.AVAILABLE, StatusTone.EMPTY, StatusTone.AVAILABLE),
            parts.filter { it.tone != StatusTone.PLAIN }.map { it.tone },
        )
    }

    @Test
    fun `empty floors render an empty line`() {
        assertEquals(emptyList<StatusPart>(), StatusLine.parts(emptyList()))
        assertEquals("", StatusLine.text(emptyList()))
    }

    @Test
    fun `parse keeps order and skips entries without a name`() {
        val payload = JSONObject(
            """
            {"floors": [
              {"device_id": "a", "name": "1F", "online": true, "free": 3},
              {"device_id": "b", "name": "", "online": true, "free": 2},
              {"device_id": "c", "online": true, "free": 1},
              {"device_id": "d", "name": "4F", "online": false, "free": null},
              {"device_id": "e", "name": "5F", "online": true, "free": null}
            ]}
            """.trimIndent(),
        )

        val floors = StatusLine.parse(payload)

        assertEquals(listOf("1F", "4F", "5F"), floors.map { it.name })
        assertEquals(listOf(3, null, null), floors.map { it.free })
        assertEquals(listOf(true, false, true), floors.map { it.online })
    }

    @Test
    fun `parse without floors gives an empty list`() {
        assertTrue(StatusLine.parse(JSONObject("{}")).isEmpty())
        assertTrue(StatusLine.parse(JSONObject("""{"floors": "bad"}""")).isEmpty())
    }
}
