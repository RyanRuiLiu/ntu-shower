package com.ryanliu.ntushower

import android.app.AlarmManager
import android.app.Application
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.service.AgentService
import java.io.File

/**
 * 崩溃自愈：未捕获异常 → 告警 1 秒后重启主服务 → 结束进程。
 * AlarmManager 跨进程死亡存活（旧系统真机验证过的模式）。
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
                alarm.setExact(AlarmManager.RTC_WAKEUP, System.currentTimeMillis() + 1000, pending)
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
    }
}
