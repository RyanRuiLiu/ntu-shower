package com.ryanliu.ntushower

import android.app.AlarmManager
import android.app.Application
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.service.AgentService
import java.io.File

/**
 * 崩溃自愈：未捕获异常 → 落 crash.log → 告警 1 秒后重启主服务 → 结束进程。
 *
 * 闹钟走 [AlarmManager.ELAPSED_REALTIME_WAKEUP] 而不是 RTC：这里要的是"1 秒之后"，
 * 不是"某个墙上时刻"。目标设备没有 NTP，系统时间会被运维改动、也会在掉电重启后跳变；
 * 用 RTC 的话时钟被往回调会让这次重启静默推迟——崩溃自愈最不该依赖一个会被改的基准。
 *
 * 这个类由 AndroidManifest 的 `android:name` 挂上，少那一行它一行都不会执行。
 */
class ShowerApp : Application() {

    override fun onCreate() {
        super.onCreate()
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            Log.e(TAG, "uncaught on ${thread.name}: ${throwable.message}", throwable)
            try {
                // 堆栈落文件：管理台 debug.exec 可直接拉取（远程无 logcat 时的问题追踪手段）
                val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
                val logFile = File(dir, "crash.log")
                if (logFile.length() > MAX_CRASH_LOG_BYTES) {
                    // 超限重置：崩溃日志的价值在近期记录，不设上限会在崩溃循环中撑爆存储
                    logFile.delete()
                }
                logFile.appendText(
                    "\n==== ${System.currentTimeMillis()} ====\n" +
                        Log.getStackTraceString(throwable) + "\n",
                )
            } catch (_: Exception) {
                // 落盘失败不能阻碍后续重启流程
            }
            try {
                val alarm = getSystemService(Context.ALARM_SERVICE) as AlarmManager
                val intent = Intent(this, AgentService::class.java)
                val pending = PendingIntent.getService(
                    this,
                    0,
                    intent,
                    PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_UPDATE_CURRENT,
                )
                alarm.setExact(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    SystemClock.elapsedRealtime() + RESTART_DELAY_MS,
                    pending,
                )
            } catch (_: Exception) {}
            defaultHandler?.uncaughtException(thread, throwable)
            android.os.Process.killProcess(android.os.Process.myPid())
            System.exit(10)
        }
        Log.i(TAG, "start, server=${BuildConfig.SERVER_DOMAIN}")
    }

    companion object {
        private const val TAG = "ShowerApp"

        /** crash.log 上限：超出即重置，避免崩溃循环把存储写满。 */
        private const val MAX_CRASH_LOG_BYTES = 256 * 1024L

        /** 崩溃到重启之间的间隔：留一点时间让系统把进程收干净。 */
        private const val RESTART_DELAY_MS = 1_000L
    }
}
