package com.ryanliu.ntushower.core.config

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.storage.AgentDatabase
import org.json.JSONObject

/**
 * 服务端下发配置的落盘副本。
 *
 * ## 它是什么，不是什么
 *
 * 服务端仍是唯一权威，这份副本只用来**缩短启动时的空窗**：设备重启后要过 DNS、TLS 与首帧
 * 往返才拿得到配置，中间那几秒（网络不通时则是一直）本该用上次的值，而不是回落到编译进
 * APK 的默认值。`hello_ack` 与 `config.update` 到达时都会把整份覆盖回来。
 *
 * ## 存的形态
 *
 * 整份 JSON 存一行，不拆成键值对：[RuntimeConfig.apply] 正好接收一个 `JSONObject`，存整份
 * 就是零转换；拆开则要再写一遍拼装，而那份拼装会成为第三个可能与 `spec.py` 漂移的地方。
 *
 * ## 失败都不外抛
 *
 * 读不出来（首次安装没有行、内容损坏）返回 null，调用方继续用默认值；写不进去也不影响本次
 * 配置已经生效——它就在内存里。落盘只关系到**下一次**启动，不该影响这一次。
 */
object ConfigCache {

    fun load(context: Context): JSONObject? = try {
        AgentDatabase.get(context).query(
            AppConfig.CONFIG_TABLE,
            arrayOf("payload"),
            null,
            null,
            null,
            null,
            null,
            "1",
        ).use { cursor -> if (cursor.moveToFirst()) JSONObject(cursor.getString(0)) else null }
    } catch (_: Exception) {
        // 见类注释：读不出来就用默认值
        null
    }

    /** 存下服务端刚下发的整份配置。 */
    fun save(context: Context, config: JSONObject) {
        try {
            val values = ContentValues().apply {
                put("id", 1)
                put("payload", config.toString())
                put("updated_at", Clock.wallClockRfc3339())
            }
            AgentDatabase.get(context).insertWithOnConflict(
                AppConfig.CONFIG_TABLE,
                null,
                values,
                SQLiteDatabase.CONFLICT_REPLACE,
            )
        } catch (_: Exception) {
            // 见类注释：这次已经生效了，落盘只关系到下次启动
        }
    }
}
