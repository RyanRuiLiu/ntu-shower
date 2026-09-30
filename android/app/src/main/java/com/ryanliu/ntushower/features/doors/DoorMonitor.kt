// 设备 ROM 冻结在 Android 4.4（API 19）：FileObserver(String, Int) 构造函数在新版 SDK
// 中废弃（改用 File 参数的重载，要求 API 29+），在 19 上是唯一可用路径。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.doors

import android.os.FileObserver
import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 事件驱动采集：监听浴室 App 数据库目录，bath.db 发生写入后触发采集回调。
 * 1 秒防抖——sqlite3 事务期间会产生多条事件，合并为一次读取。
 *
 * 掩码必须含 MODIFY：浴室 App 长持数据库 fd，事务写入只触发 MODIFY 不触发
 * CLOSE_WRITE（真机实测漏报教训，见 docs/ARCHITECTURE.md）。
 */
class DoorMonitor(private val onChange: () -> Unit) :
    FileObserver(AppConfig.BATH_DB_DIR, CLOSE_WRITE or MODIFY or DELETE or MOVED_TO) {

    private val triggered = AtomicBoolean(false)

    override fun onEvent(event: Int, path: String?) {
        if (!DoorEventFilter.isRelevant(path, AppConfig.BATH_DB_FILE)) return
        if (!triggered.compareAndSet(false, true)) return
        Log.d(TAG, "db event 0x${event.toString(16)}, capture scheduled")
        Thread {
            try {
                Thread.sleep(RuntimeConfig.reportDebounceMs)
                onChange()
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
            } finally {
                triggered.set(false)
            }
        }.start()
    }

    companion object {
        private const val TAG = "DoorMonitor"
    }
}
