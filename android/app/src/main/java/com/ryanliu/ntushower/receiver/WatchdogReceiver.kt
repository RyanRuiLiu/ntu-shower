package com.ryanliu.ntushower.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.ryanliu.ntushower.service.AgentService

/** 看门狗：定时拉活服务，对存活服务为幂等调用。 */
class WatchdogReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        context.startService(Intent(context, AgentService::class.java))
    }
}
