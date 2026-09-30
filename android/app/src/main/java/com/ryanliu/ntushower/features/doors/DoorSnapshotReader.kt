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
        val result = RootShell.exec("echo '$SQL' | sqlite3 -separator '|' ${AppConfig.BATH_DB_PATH}")
        val elapsed = SystemClock.elapsedRealtime() - startedAt
        if (!result.success) {
            Log.w(TAG, "read failed (${elapsed}ms)")
            return emptyList()
        }
        val doors = DoorRaw.parseRows(result.output)
        PerfLogger.log(TAG, "sqlite3 read", elapsed)
        return doors
    }
}
