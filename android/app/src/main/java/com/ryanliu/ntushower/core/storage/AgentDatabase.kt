package com.ryanliu.ntushower.core.storage

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import com.ryanliu.ntushower.core.config.AppConfig

/**
 * 应用私有库的唯一入口。
 *
 * 装的是本应用自己的持久状态，两张表：
 * - `door_outbox`——待确认的喷头快照；
 * - `config_effective`——服务端最后下发的配置，供下次启动直接使用。
 *
 * 位置与命名见 [com.ryanliu.ntushower.core.config.AppConfig] 的库常量。
 *
 * 入口收在这里的理由是**建表只发生一次**。两张表分属 doors 与 config 两个模块，各自
 * `openOrCreateDatabase` 会让"库里有哪些表"散成几处，加一张表要记得改几个地方。
 *
 * 连接在进程内复用、不主动关闭：服务被重建时进程往往还活着，关掉它会让下一次使用需要重开，
 * 而重开期间的写入会落到哪儿并不确定。进程结束时由系统回收。
 */
object AgentDatabase {

    @Volatile
    private var opened: SQLiteDatabase? = null

    /** 取库连接；首次调用时建表。 */
    fun get(context: Context): SQLiteDatabase {
        opened?.let { return it }
        return synchronized(this) {
            opened ?: create(context.applicationContext).also { opened = it }
        }
    }

    private fun create(context: Context): SQLiteDatabase =
        context.openOrCreateDatabase(AppConfig.AGENT_DB_FILE, Context.MODE_PRIVATE, null).also { db ->
            // 列命名与服务端一致：主键叫 `id` 不叫 `seq`，时间字段带 `_at` 后缀。
            //
            // `created_at` 不是可有可无的：排查"快照积压了多久"只能靠它，而积压时长决定了
            // 是网络问题还是服务端没回 ack。
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS ${AppConfig.OUTBOX_TABLE} (" +
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                    "snapshot_id TEXT NOT NULL UNIQUE, " +
                    "payload TEXT NOT NULL, " +
                    "created_at TEXT NOT NULL)",
            )
            // 只允许一行：配置是"这台设备当前该用的那一份"，不是历史序列。`id = 1` 的约束
            // 比"约定只存一行"可靠——它让第二行根本插不进来。
            //
            // 不存 revision：hello_ack 与 config.update 里只有后者带它，存下来会是"有时 0、
            // 有时是真值"，读的人无从判断该不该信。要诊断"设备拿着哪一版"，看服务端那侧即可。
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS ${AppConfig.CONFIG_TABLE} (" +
                    "id INTEGER PRIMARY KEY CHECK (id = 1), " +
                    "payload TEXT NOT NULL, " +
                    "updated_at TEXT NOT NULL)",
            )
        }
}
