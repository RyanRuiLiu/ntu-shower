package com.ryanliu.ntushower.transport

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MessageCodecTest {

    @Test
    fun encode_containsEnvelopeFields() {
        val raw = MessageCodec.encode("door.snapshot", JSONObject().put("k", "v"))
        val obj = JSONObject(raw)
        assertEquals(1, obj.getInt("v"))
        assertEquals("door.snapshot", obj.getString("type"))
        assertTrue(obj.getString("id").isNotEmpty())
        assertTrue(obj.getString("ts").endsWith("Z"))
    }

    @Test
    fun encode_replyToIsPreserved() {
        val raw = MessageCodec.encode("config.update_ack", JSONObject(), replyTo = "cmd-1")
        assertTrue(JSONObject(raw).getString("reply_to") == "cmd-1")
    }

    @Test
    fun decode_roundtrip() {
        val raw = MessageCodec.encode("link.ping", JSONObject().put("sender_time", "t"))
        val env = MessageCodec.decode(raw)!!
        assertEquals("link.ping", env.type)
        assertEquals("t", env.payload.getString("sender_time"))
        assertNull(env.replyTo)
    }

    @Test
    fun decode_malformedReturnsNull() {
        assertNull(MessageCodec.decode("not json"))
        assertNull(MessageCodec.decode("{}"))
    }

    @Test
    fun decode_rejectsWrongProtocolVersion() {
        // 版本不匹配必须整帧丢弃：字段语义可能已变，猜测比丢掉更危险
        assertNull(MessageCodec.decode("""{"v":2,"type":"link.ping","id":"x"}"""))
        assertNull(MessageCodec.decode("""{"type":"link.ping","id":"x"}"""))
    }

    @Test
    fun decode_rejectsMissingTypeOrId() {
        assertNull(MessageCodec.decode("""{"v":1,"type":"","id":"x"}"""))
        assertNull(MessageCodec.decode("""{"v":1,"type":"link.ping","id":""}"""))
    }

    @Test
    fun decode_treatsNullReplyToAsAbsent() {
        val env = MessageCodec.decode("""{"v":1,"type":"link.ping","id":"x","reply_to":null}""")

        assertNull(env?.replyTo)
    }

    @Test
    fun decode_keepsReplyToWhenPresent() {
        val env = MessageCodec.decode("""{"v":1,"type":"config.update_ack","id":"x","reply_to":"cmd-9"}""")

        assertEquals("cmd-9", env?.replyTo)
    }

    @Test
    fun decode_defaultsMissingFields() {
        val env = MessageCodec.decode("""{"v":1,"type":"link.ping","id":"x"}""")

        assertEquals("", env?.ts)
        assertEquals(0, env?.payload?.length())
    }

    @Test
    fun decode_ignoresNonObjectPayload() {
        // payload 不是对象时按空对象处理，不让整帧失效
        val env = MessageCodec.decode("""{"v":1,"type":"link.ping","id":"x","payload":"nope"}""")

        assertEquals(0, env?.payload?.length())
    }
}
