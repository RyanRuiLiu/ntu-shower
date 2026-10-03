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
 *
 * ## 采集期间来的事件不能丢
 *
 * 一次采集从防抖等待开始、经读取与入队、到 [onChange] 返回为止，占住闸门几百毫秒。
 * 这期间到达的事件**必须记一笔并在跑完后补一次**，而不是丢掉：那几百毫秒里落进来的，
 * 很可能正是某位用户「释放／清零」的那一帧。丢了它，这个喷头在网页端会整夜显示"淋浴中"，
 * 服务端那条会话也一直不结束、时长持续累加——直到任意一个喷头下次写库才被纠正。
 */
class DoorMonitor(private val onChange: () -> Unit) :
    FileObserver(AppConfig.BATH_DB_DIR, CLOSE_WRITE or MODIFY or DELETE or MOVED_TO) {

    /** 当前是否有一轮采集在跑（从防抖等待开始，到 [onChange] 返回为止）。 */
    private val running = AtomicBoolean(false)

    /** 跑的过程里又来过事件：记一笔，收尾时补跑。 */
    private val pending = AtomicBoolean(false)

    override fun onEvent(event: Int, path: String?) {
        if (!DoorEventFilter.isRelevant(path, AppConfig.BATH_DB_FILE)) return
        if (!running.compareAndSet(false, true)) {
            pending.set(true)
            return
        }
        Log.d(TAG, "db event 0x${event.toString(16)}, capture scheduled")
        Thread {
            try {
                Thread.sleep(RuntimeConfig.reportDebounceMs)
                // 防抖等待期间攒下的事件在这里一次消化掉
                do {
                    pending.set(false)
                    onChange()
                } while (pending.get())
            } catch (e: InterruptedException) {
                Thread.currentThread().interrupt()
            } finally {
                running.set(false)
                // 循环退出与放开闸门之间还有一个更窄的窗口：那时事件已经设了 pending，
                // 却没有人再回头看它。这里自己再起一轮，把它交出去。
                if (pending.getAndSet(false)) onEvent(0, AppConfig.BATH_DB_FILE)
            }
        }.start()
    }

    companion object {
        private const val TAG = "DoorMonitor"
    }
}
