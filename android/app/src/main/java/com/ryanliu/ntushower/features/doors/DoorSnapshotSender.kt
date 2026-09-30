package com.ryanliu.ntushower.features.doors

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.transport.GatewayClient
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.util.UUID

/**
 * 快照发件箱：本地 SQLite 按序保存未确认快照，收到 door.snapshot_ack 才删除；
 * 未确认的按序重发（断网不丢数据，恢复后续传）。
 *
 * 去重：hashCode 比对门状态列表，内容未变化不上报（浴室 App 占用期每秒写库，
 * 无去重会造成每秒一次的无效快照，旧系统同款设计）。
 */
class DoorSnapshotSender(context: Context) {

    /** 发件箱里最旧的一条待确认快照。 */
    private data class OutboxRow(val id: Long, val snapshotId: String, val payload: String)

    private val db: SQLiteDatabase =
        context.openOrCreateDatabase(AppConfig.OUTBOX_DB_FILE, Context.MODE_PRIVATE, null).also {
            // 表名与库同名（库里只有这一张表）；字段命名与服务端一致——主键叫 `id`
            // 不叫 `seq`，时间字段带 `_at` 后缀。
            //
            // `created_at` 不是可有可无的：排查"快照积压了多久"只能靠它，而积压时长
            // 决定了是网络问题还是服务端没回 ack。
            it.execSQL(
                "CREATE TABLE IF NOT EXISTS ${AppConfig.OUTBOX_TABLE} (" +
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                    "snapshot_id TEXT NOT NULL UNIQUE, " +
                    "payload TEXT NOT NULL, " +
                    "created_at TEXT NOT NULL)",
            )
        }

    /**
     * 上一次上报的门状态指纹；`null` 表示"还没有指纹"。
     *
     * 用可空而不是某个具体的哨兵值：`hashCode()` 的取值范围是整个 Int，拿任何一个数字
     * 当"没有"，都可能与真实指纹撞上。
     */
    @Volatile private var lastHash: Int? = null

    /**
     * 采集并入队；有变化才会入队，入队后立即尝试发送。
     *
     * 注意：门状态无变化时本方法直接返回（去重），因此**不能**把它当作重传入口——
     * 断网期间积压的快照与门状态是否变化无关，重连时必须直接调 [flush]。
     */
    fun captureAndEnqueue(gateway: GatewayClient) {
        val doors = DoorSnapshotReader.readAll()
        if (doors.isEmpty()) return
        val hash = doors.hashCode()
        if (hash == lastHash) return
        lastHash = hash
        val snapshotId = UUID.randomUUID().toString()
        val payload = JSONObject()
            .put("snapshot_id", snapshotId)
            .put("source", "db_watch")
            .put("doors", JSONArray().apply { doors.forEach { put(it.toPayload()) } })
        db.insertWithOnConflict(
            AppConfig.OUTBOX_TABLE,
            null,
            ContentValues().apply {
                put("snapshot_id", snapshotId)
                put("payload", payload.toString())
                put("created_at", Clock.nowRfc3339())
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
        Log.i(TAG, "snapshot $snapshotId enqueued (${doors.size} doors)")
        flush(gateway)
    }

    /**
     * 忘掉内容指纹，让下一次采集无条件入队。
     *
     * 重连后要调它。服务端的 `door_live` 会被清空重建（开发期"删了重建"是常规操作），
     * 而设备侧门状态可能一直没变、发件箱也早空了——不重置指纹的话，那扇门在网页端会
     * 一直空白，直到任意一扇门状态变化；浴室没人用的时段，可能几个小时都没有变化。
     */
    fun forgetLastState() {
        lastHash = null
    }

    /**
     * 按序发送最旧的一条未确认快照（等待 ack 后再发下一条）。幂等，可随时调用。
     *
     * 遇到解析不出 JSON 的坏行**当场删掉再取下一条**，而不是把异常抛出去。坏行永远排在
     * 最前面（按 id 升序取最旧），每次 flush 都在同一行上失败，后面所有快照全被它堵住
     * ——这台设备的门状态上报就此彻底停止，而日志里只有一串 JSON 异常，读不出"上报停了"。
     *
     * 存储损坏才会产生坏行（单条 INSERT 是原子的），概率低，但一旦出现不会自愈。
     */
    fun flush(gateway: GatewayClient) {
        while (true) {
            val row = readOldest() ?: return
            val payload = try {
                JSONObject(row.payload)
            } catch (e: JSONException) {
                Log.e(TAG, "载荷解析失败，删除该行后继续：${row.snapshotId} (${e.message})")
                db.delete(AppConfig.OUTBOX_TABLE, "id = ?", arrayOf(row.id.toString()))
                continue
            }
            Log.d(TAG, "sending snapshot ${row.snapshotId}")
            gateway.send("door.snapshot", payload)
            return
        }
    }

    /** 收到 ack：删除对应快照，继续发下一条。 */
    fun onAcked(snapshotId: String, gateway: GatewayClient) {
        db.delete(AppConfig.OUTBOX_TABLE, "snapshot_id = ?", arrayOf(snapshotId))
        Log.i(TAG, "snapshot acked: $snapshotId")
        flush(gateway)
    }

    private fun readOldest(): OutboxRow? = db
        .query(AppConfig.OUTBOX_TABLE, null, null, null, null, null, "id ASC", "1")
        .use { c ->
            if (!c.moveToFirst()) return null
            OutboxRow(
                id = c.getLong(c.getColumnIndexOrThrow("id")),
                snapshotId = c.getString(c.getColumnIndexOrThrow("snapshot_id")),
                payload = c.getString(c.getColumnIndexOrThrow("payload")),
            )
        }

    companion object {
        private const val TAG = "DoorSnapshotSender"
    }
}
