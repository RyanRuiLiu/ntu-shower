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
        val raw = MessageCodec.encode("config.ack", JSONObject(), replyTo = "cmd-1")
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
}
