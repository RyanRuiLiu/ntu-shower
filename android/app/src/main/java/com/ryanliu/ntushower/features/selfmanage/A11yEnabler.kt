package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.shell.RootShell

/**
 * 无障碍服务启用：root 写系统设置（append 语义，保留已有条目，不碰旧应用）。
 * 服务启动时调用，幂等。
 */
object A11yEnabler {

    private const val TAG = "A11yEnabler"
    private const val APP_PACKAGE = "com.ryanliu.ntushower"
    private const val COMPONENT = "$APP_PACKAGE/.service.A11yService"

    fun ensureEnabled(): Boolean {
        val current = RootShell.exec(
            "settings get secure enabled_accessibility_services",
        ).output.trim()
        if (A11ySettingsMerge.contains(current, APP_PACKAGE)) {
            return true
        }
        val merged = A11ySettingsMerge.mergeSettingsValue(current, COMPONENT)

        val result = RootShell.exec(
            "settings put secure enabled_accessibility_services \"$merged\" && " +
                "settings put secure accessibility_enabled 1",
        )
        Log.i(TAG, "a11y enable: ${result.success} -> $merged")
        return result.success
    }
}
