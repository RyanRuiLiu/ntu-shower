package com.ryanliu.ntushower.features.doors

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.transport.GatewayClient
import org.json.JSONArray
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

    /** 发件箱中的一条待确认快照。 */
    private data class PendingSnapshot(val snapshotId: String, val payload: JSONObject)

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

    @Volatile private var lastHash = 0

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

    /** 按序发送最旧的一条未确认快照（等待 ack 后再发下一条）。幂等，可随时调用。 */
    fun flush(gateway: GatewayClient) {
        val oldest = db
            .query(AppConfig.OUTBOX_TABLE, null, null, null, null, null, "id ASC", "1")
            .use { c ->
                if (c.moveToFirst()) {
                    PendingSnapshot(
                        snapshotId = c.getString(c.getColumnIndexOrThrow("snapshot_id")),
                        payload = JSONObject(c.getString(c.getColumnIndexOrThrow("payload"))),
                    )
                } else {
                    null
                }
            } ?: return
        Log.d(TAG, "sending snapshot ${oldest.snapshotId}")
        gateway.send("door.snapshot", oldest.payload)
    }

    /** 收到 ack：删除对应快照，继续发下一条。 */
    fun onAcked(snapshotId: String, gateway: GatewayClient) {
        db.delete(AppConfig.OUTBOX_TABLE, "snapshot_id = ?", arrayOf(snapshotId))
        Log.i(TAG, "snapshot acked: $snapshotId")
        flush(gateway)
    }

    companion object {
        private const val TAG = "DoorSnapshotSender"
    }
}
