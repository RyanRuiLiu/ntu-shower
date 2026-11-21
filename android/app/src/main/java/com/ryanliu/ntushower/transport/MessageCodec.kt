package com.ryanliu.ntushower.transport

import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.ServerClock
import org.json.JSONObject
import java.util.UUID

/** 解码后的信封（对应 protocol/schema/envelope.json）。 */
data class Envelope(
    val v: Int,
    val type: String,
    val id: String,
    val ts: String,
    val replyTo: String?,
    val payload: JSONObject,
)

/** 信封编解码，规则与 protocol/schema 一一对应。 */
object MessageCodec {

    fun encode(type: String, payload: JSONObject, replyTo: String? = null): String {
        val env = JSONObject()
            .put("v", 1)
            .put("type", type)
            .put("id", UUID.randomUUID().toString())
            // 帧时刻在服务端时间轴上：它要与服务端审计、日志按时间对账，而墙钟在
            // hold 期间是故意拨快的。无锚点（刚启动还没握手）回退墙钟，宁可不准
            // 也不缺字段。
            .put("ts", ServerClock.nowRfc3339() ?: Clock.wallClockRfc3339())
            .put("payload", payload)
        if (replyTo != null) {
            env.put("reply_to", replyTo)
        }
        return env.toString()
    }

    fun decode(raw: String): Envelope? {
        return try {
            val o = JSONObject(raw)
            if (o.optInt("v", 0) != 1 || o.optString("type").isEmpty() || o.optString("id").isEmpty()) {
                return null
            }
            Envelope(
                v = o.getInt("v"),
                type = o.getString("type"),
                id = o.getString("id"),
                ts = o.optString("ts"),
                replyTo = if (o.has("reply_to") && !o.isNull("reply_to")) o.getString("reply_to") else null,
                payload = o.optJSONObject("payload") ?: JSONObject(),
            )
        } catch (e: Exception) {
            null
        }
    }
}
