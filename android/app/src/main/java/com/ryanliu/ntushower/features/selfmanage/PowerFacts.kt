package com.ryanliu.ntushower.features.selfmanage

import com.ryanliu.ntushower.core.config.AppConfig
import java.io.File

/**
 * 设备本地电源计划事实：浴室 App 从 V8 平台拿到的每日开关机时刻，与本机时区偏移。
 *
 * 服务端的 hold 对账、MCU 常规重布与语音的当地时刻全部按这份事实换算——**时刻由
 * 设备侧自报，服务端不定任何时刻**。来源是浴室 App 下载参数时落在
 * `/sdcard/datalook/log/Params_*.txt` 里的 `autoCloseTime` / `autoOpenTime`
 * （真机实测行：`读取参数[autoOpenTime:10:55][autoCloseTime:23:58]`）。
 *
 * 读不到（App 从未启动、目录为空）如实给 null：服务端按"计划未知"处理，不发明默认值。
 */
data class PowerFacts(val autoCloseTime: String, val autoOpenTime: String, val zoneOffsetMinutes: Int) {
    /** 上报载荷：device.status 帧的 payload。 */
    fun toPayload(): Map<String, Any> = mapOf(
        "auto_close_time" to autoCloseTime,
        "auto_open_time" to autoOpenTime,
        "zone_offset_minutes" to zoneOffsetMinutes,
    )

    /** 内容指纹：变化检测用（与 DoorSnapshotSender.lastHash 同一手法）。 */
    fun fingerprint(): Int = listOf(autoCloseTime, autoOpenTime, zoneOffsetMinutes).hashCode()

    companion object {
        /** 参数行里 `autoCloseTime` / `autoOpenTime` 的取值（`HH:MM`）。 */
        private val AUTO_CLOSE = Regex("""\[autoCloseTime:(\d{2}:\d{2})]""")
        private val AUTO_OPEN = Regex("""\[autoOpenTime:(\d{2}:\d{2})]""")

        /** 从参数目录读最新一份事实；文件缺失或解析不出给 null（如实上报"未知"）。 */
        fun read(paramsDir: File = File(AppConfig.BATH_PARAMS_DIR)): PowerFacts? {
            val latest = paramsDir
                .listFiles { file -> file.name.startsWith("Params_") }
                ?.maxByOrNull { it.name } ?: return null
            val text = runCatching { latest.readText() }.getOrNull() ?: return null
            val close = AUTO_CLOSE.find(text)?.groupValues?.get(1) ?: return null
            val open = AUTO_OPEN.find(text)?.groupValues?.get(1) ?: return null
            val zone = java.util.TimeZone.getDefault().rawOffset / 60_000
            return PowerFacts(autoCloseTime = close, autoOpenTime = open, zoneOffsetMinutes = zone)
        }
    }
}
