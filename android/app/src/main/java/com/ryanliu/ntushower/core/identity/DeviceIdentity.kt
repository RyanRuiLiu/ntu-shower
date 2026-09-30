package com.ryanliu.ntushower.core.identity

import android.content.Context
import android.provider.Settings

/**
 * 设备身份：device_id 取自系统 ANDROID_ID。
 * 重装 APK 不变；恢复出厂会变——设备变成"新设备"，需在管理后台重新信任（见 protocol/schema/）。
 * 不落文件、不做混淆加密。
 */
object DeviceIdentity {

    fun deviceId(context: Context): String {
        val id = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        if (id.isNullOrBlank()) {
            throw IllegalStateException("ANDROID_ID unavailable")
        }
        return id
    }
}
