package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.shell.RootShell
import java.io.File

/**
 * 浴室 App 的进程级重启（root）：先强停，再冷启动。
 *
 * 服务端动作 `bath.restart`（网页调试按钮）与开机时间校正（TimeSync）共用这一个
 * 实现：强停后必须**冷启动**而不是切回前台——时钟校正后浴室 App 手里的旧开关机
 * 计划要在重新装载时才会被替换。
 *
 * 强停会中断进行中的洗浴会话：调用方负责把关。网页端动作有确认弹窗；开机校正
 * 发生在设备刚上电时，不会有会话进行。
 */
object BathAppRestart {

    private const val BATH_PACKAGE = "com.datalook.bath"

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 强停并冷启动浴室 App。
     *
     * shell 提交成功即视为成功——冷启动本身由系统异步完成，成败由 [BathAppGuard]
     * 的事件流观察（回到前台即 onForeground）。结果落 device.log，与命令审计可按
     * 时间对上。
     */
    fun restart(): Boolean {
        deviceLog.lifecycle("bath.restart-started", BATH_PACKAGE)
        val result = RootShell.control.exec(
            "am force-stop $BATH_PACKAGE; sleep 1; " +
                "monkey -p $BATH_PACKAGE -c android.intent.category.LAUNCHER 1",
        )
        val ok = result.exitCode == 0
        if (ok) {
            deviceLog.lifecycle("bath.restarted", BATH_PACKAGE)
        } else {
            deviceLog.error("bath.restart-failed", result.output)
        }
        return ok
    }
}
