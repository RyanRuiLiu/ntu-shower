package com.ryanliu.ntushower.features.doors

import android.os.SystemClock
import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.perf.PerfLogger
import com.ryanliu.ntushower.core.shell.RootShell

/** bath.db 全量读取：每次独立 spawn sqlite3，避免持有数据库读锁（旧系统实测论证过的取舍）。 */
object DoorSnapshotReader {

    private const val TAG = "DoorSnapshotReader"

    private const val SQL =
        "SELECT doorNo, cardno, certno, accno, openTime, inTime, closeTime, bathTime, status" +
            " FROM t_doorlist ORDER BY doorNo;"

    fun readAll(): List<DoorRaw> {
        val startedAt = SystemClock.elapsedRealtime()
        // 走数据通道：这条命令是生命线（数据库一变就触发），与界面注入、远程命令
        // 共用通道会让它被无关命令拖住，而用户正看着喷头状态挑喷头
        val result = RootShell.data.exec("echo '$SQL' | sqlite3 -separator '|' ${AppConfig.BATH_DB_PATH}")
        val elapsed = SystemClock.elapsedRealtime() - startedAt
        if (result.exitCode != 0) {
            Log.w(TAG, "read failed (${elapsed}ms)")
            return emptyList()
        }
        val doors = DoorRaw.parseRows(result.output)
        PerfLogger.log(TAG, "sqlite3 read", elapsed)
        return doors
    }
}
