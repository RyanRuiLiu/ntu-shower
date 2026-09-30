package com.ryanliu.ntushower.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.ryanliu.ntushower.service.AgentService

/**
 * 覆盖安装/OTA 自启入口：
 * - MY_PACKAGE_REPLACED：系统定向投递给被替换的应用本身（stopped 状态也送达，
 *   OTA 与手动重装均已真机验证）；
 * - BOOT_COMPLETED：标准 ROM 的开机自启路径。注意：目标设备 DS83X 的定制 ROM
 *   实测不投递开机广播（旧项目同结论），该设备上开机拉起由无障碍重绑锚点承担
 *   （见 A11yService 自举）；标准 ROM 走这条路径。
 *
 * 多路径并发拉起无冲突：AgentService.onStartCommand 有一次性初始化守卫。
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> start(context, intent.action)
            Intent.ACTION_MY_PACKAGE_REPLACED -> start(context, intent.action)
        }
    }

    private fun start(context: Context, action: String?) {
        Log.i(TAG, "starting AgentService (action=$action)")
        context.startService(Intent(context, AgentService::class.java))
    }

    companion object {
        private const val TAG = "BootReceiver"
    }
}
