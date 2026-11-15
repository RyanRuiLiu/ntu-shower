package com.ryanliu.ntushower.features.doors

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.ServerClock
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.storage.AgentDatabase
import com.ryanliu.ntushower.transport.GatewayClient
import com.ryanliu.ntushower.transport.MessageTypes
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.util.UUID

/**
 * 帧发件箱：本地 SQLite 按序保存未确认帧，收到 door.snapshot_ack 才删除；
 * 未确认的按序重发（断网不丢数据，恢复后续传）。
 *
 * 去重：逐扇喷头取 [DoorRaw.fingerprint] 再比对，内容未变化不上报。
 *
 * 上报因此只发生在**状态迁移**那一刻（进人、开阀、关阀暂停、恢复、出门清零）——这与
 * 浴室 App 的写库节奏一致：它洗浴期间根本不写，只在迁移时落 SQL（见
 * `ntu-bathapp-reverse/notes/2026-06-21-doorlist-schema.md`）。实测 100 天的平均上报量是每 4 分钟一份。
 */
class DoorSnapshotSender(context: Context, private val onDoorsRead: (DoorReading) -> Unit = {}) {

    /** 发件箱里最旧的一条待确认帧。 */
    private data class OutboxRow(val id: Long, val snapshotId: String, val payload: String)

    /**
     * 应用上下文；发件箱只在工作线程读写，构造发生在主线程，所以连接推迟到第一次使用，
     * 不在构造期打开数据库。
     */
    private val appContext = context.applicationContext
    private val db: SQLiteDatabase by lazy { AgentDatabase.get(appContext) }

    /**
     * 上一次上报的喷头状态指纹；`null` 表示"还没有指纹"。
     *
     * 用可空而不是某个具体的哨兵值：`hashCode()` 的取值范围是整个 Int，拿任何一个数字
     * 当"没有"，都可能恰与真实指纹相同而误判。
     */
    @Volatile private var lastHash: Int? = null

    /**
     * 采集并入队；有变化才会入队，入队后立即尝试发送。
     *
     * [source] 由调用方如实标注这一帧为什么产生，服务端据此区分"事件"与"全貌"：
     * - `db_watch`——浴室 App 写库触发，状态真的变了；
     * - `startup`——重连或开机后推的一遍全貌，与状态是否变化无关（见 [forgetLastState]）。
     *
     * 注意：喷头状态无变化时本方法直接返回（去重），因此**不能**把它当作重传入口——
     * 断网期间积压的帧与喷头状态是否变化无关，重连时必须直接调 [flush]。
     *
     * 读库返回空也照常入队：设备端的职责是如实送达"库现在是什么"，而"读到空"本身
     * 就是一条事实（这台设备的 t_doorlist 恒有它的全部喷头行），由服务端判断它意味着
     * 什么。此前在这里直接返回，结果是服务端现态一直停在旧值，而设备端与日志里都看
     * 不出发生过这件事。
     */
    fun captureAndEnqueue(gateway: GatewayClient, source: String) {
        // 两个读数在同一瞬间取，它们分工不同：
        // - capturedAt 是这一帧的**权威时刻**，由单调钟加服务端锚点算出，拨钟影响不到它；
        // - deviceClock 是设备墙钟**此刻的读数**，服务端拿它与 capturedAt 相减得到偏差，
        //   再用那个偏差把库里的设备轴时刻平移过来。
        // 差一个调用栈的延迟就会让偏差估计带上那个延迟，而这里的精度单位是毫秒。
        //
        // 时刻取在读库之前：采到的内容是那条 SELECT 建立读视图那一刻的数据库，读完
        // 再取会把读的耗时算进去（真机实测几百毫秒），而这是个恒定偏晚的系统偏差。
        val capturedAt = ServerClock.nowRfc3339()
        val deviceClock = Clock.wallClockRfc3339()
        val reading = DoorSnapshotReader.readAll()
        // 先通知读数观察者，再判内容指纹：屏幕提示的占用条件依赖“这次读到了什么”，
        // 与“这一帧是否值得上报”是两件事。
        onDoorsRead(reading)
        val doors = reading.doors
        val hash = doors.map { it.fingerprint() }.hashCode()
        if (hash == lastHash) return
        lastHash = hash
        val snapshotId = UUID.randomUUID().toString()
        val payload = JSONObject()
            .put("snapshot_id", snapshotId)
            .put("source", source)
            .put("doors", JSONArray().apply { doors.forEach { put(it.toPayload()) } })
        if (capturedAt != null) {
            payload.put("captured_at", capturedAt)
        }
        payload.put("device_clock", deviceClock)
        val rowId = db.insertWithOnConflict(
            AppConfig.OUTBOX_TABLE,
            null,
            ContentValues().apply {
                put("snapshot_id", snapshotId)
                put("payload", payload.toString())
                put("created_at", Clock.wallClockRfc3339())
            },
            SQLiteDatabase.CONFLICT_REPLACE,
        )
        // 序号取自增主键：它单调，且只在最旧的被裁掉时才跳号，服务端据此看得出"中间
        // 少了 N 条"。载荷定稿在插入之前，所以回填一次。
        if (rowId > 0) {
            payload.put("seq", rowId)
            db.update(
                AppConfig.OUTBOX_TABLE,
                ContentValues().apply { put("payload", payload.toString()) },
                "id = ?",
                arrayOf(rowId.toString()),
            )
        }
        Log.i(TAG, "snapshot $snapshotId enqueued (${doors.size} doors, source=$source)")
        trimOutbox()
        flush(gateway)
    }

    /**
     * 把发件箱压在 [RuntimeConfig.outboxMaxRows] 行以内，超出丢最旧的。
     *
     * 断网期间每有一次喷头状态变化就多一行，而只有收到 ack 才会删。上限是**配置项**（默认值与
     * 服务端登记表同源，那边刻意给得远超真实所需），因此正常情况下它一次也不会触发——留着
     * 它是给极端积压一个终点，而不是当作日常淘汰策略。调小它是主动放弃历史：帧丢了设备
     * 不会重发，服务端的时长与状态迁移就永久缺一块。
     *
     * 丢最旧的而不是拒绝新的：越新的帧越接近当前实情，而服务端的现态本来就由最后一条决定。
     */
    private fun trimOutbox() {
        val limit = RuntimeConfig.outboxMaxRows
        val total = countRows()
        if (total <= limit) return
        val excess = total - limit
        db.execSQL(
            "DELETE FROM ${AppConfig.OUTBOX_TABLE} WHERE id IN " +
                "(SELECT id FROM ${AppConfig.OUTBOX_TABLE} ORDER BY id ASC LIMIT ?)",
            arrayOf<Any>(excess),
        )
        Log.w(TAG, "outbox trimmed: dropped $excess oldest rows")
    }

    private fun countRows(): Int = db
        .rawQuery("SELECT COUNT(*) FROM ${AppConfig.OUTBOX_TABLE}", null)
        .use { c -> if (c.moveToFirst()) c.getInt(0) else 0 }

    /**
     * 忘掉内容指纹，让下一次采集无条件入队。
     *
     * 重连后要调它。服务端的 `door_live` 会被清空重建（开发期"删了重建"是常规操作），
     * 而设备侧喷头状态可能一直没变、发件箱也早空了——不重置指纹的话，那个喷头在网页端会
     * 一直空白，直到任意一个喷头状态变化；浴室没人用的时段，可能几个小时都没有变化。
     */
    fun forgetLastState() {
        lastHash = null
    }

    /**
     * 只读一次并通知读数观察者；不更新指纹、不写发件箱。
     *
     * 供屏幕提示在**尚未收到过完整读数**时补读一次（看门狗路径，见
     * AgentService.syncConfigDependents）：占用是本地事实，不应因为还没采集到
     * 完整读数就变成未知。喷头状态变化本身的采集入队一律走 [captureAndEnqueue]，
     * 与是否 `trusted` 无关——本方法不再承担"未连接时判断占用"的职责。
     * 与 [captureAndEnqueue] 共用调用方的工作线程，天然串行。
     */
    fun readAndNotify() {
        onDoorsRead(DoorSnapshotReader.readAll())
    }

    /**
     * 按序发送最旧的一条未确认帧（等待 ack 后再发下一条）。幂等，可随时调用。
     *
     * 遇到解析不出 JSON 的坏行**当场删掉再取下一条**，而不是把异常抛出去。坏行永远排在
     * 最前面（按 id 升序取最旧），每次 flush 都在同一行上失败，后面所有帧全被它堵住
     * ——这台设备的喷头状态上报就此彻底停止，而日志里只有一串 JSON 异常，读不出"上报停了"。
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
            gateway.send(MessageTypes.DOOR_SNAPSHOT, payload)
            return
        }
    }

    /** 收到 ack：删除对应帧，继续发下一条。 */
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

        /** 采集来源：浴室 App 写库触发，状态真的变了。 */
        const val SOURCE_DB_WATCH = "db_watch"

        /** 采集来源：重连或开机后推的一遍全貌，与状态是否变化无关。 */
        const val SOURCE_STARTUP = "startup"
    }
}
