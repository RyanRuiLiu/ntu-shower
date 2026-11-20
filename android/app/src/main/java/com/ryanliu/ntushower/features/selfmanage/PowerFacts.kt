package com.ryanliu.ntushower.features.selfmanage

import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.shell.RootShell
import java.util.TimeZone

/**
 * 设备本地电源计划事实：浴室 App 从 V8 平台拿到的每日开关机时刻，与本机时区偏移。
 *
 * 服务端的 hold 对账、MCU 常规重布与语音的当地时刻全部按这份事实换算——**时刻由
 * 设备侧自报，服务端不定任何时刻**。来源是浴室 App 下载参数时落在
 * `/sdcard/datalook/log/Params_*.txt` 里的 `autoCloseTime` / `autoOpenTime`
 * （真机实测行：`读取参数[autoOpenTime:10:55][autoCloseTime:23:58]`）。
 *
 * 参数文件属 root:battery 且不可被本进程读取：目录列举与文件读取一律经 root
 * shell（与 DoorSnapshotReader 读 bath.db 同一前提）。读不到（App 从未启动、目录
 * 为空）如实给 null：服务端按"计划未知"处理，不发明默认值。
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

        /** 取目录里最新（名字最大 = 日期最新）的一份参数文件并解析。
         *  [ls] 与 [readFile] 是注入的 root 读取函数：生产绑定 RootShell，测试注入纯函数。 */
        fun read(
            paramsDir: String = AppConfig.BATH_PARAMS_DIR,
            ls: (String) -> List<String> = ::rootLs,
            readFile: (String) -> String? = ::rootRead,
        ): PowerFacts? {
            val latestName = ls(paramsDir)
                .filter { it.startsWith("Params_") }
                .maxOrNull() ?: return null
            val text = readFile("$paramsDir/$latestName") ?: return null
            val close = AUTO_CLOSE.find(text)?.groupValues?.get(1) ?: return null
            val open = AUTO_OPEN.find(text)?.groupValues?.get(1) ?: return null
            val zone = TimeZone.getDefault().rawOffset / 60_000
            return PowerFacts(autoCloseTime = close, autoOpenTime = open, zoneOffsetMinutes = zone)
        }

        /** 经 root shell 列目录（每行一个名字）；shell 不可用或失败给空表。 */
        private fun rootLs(dir: String): List<String> = try {
            val result = RootShell.control.exec("ls -1 $dir")
            result.output.takeIf { result.exitCode == 0 && !result.timedOut }
                ?.split("\n")
                ?.map { it.trim() }
                ?.filter { it.isNotEmpty() }
                ?: emptyList()
        } catch (_: Exception) {
            emptyList()
        }

        /** 经 root shell 读文件全文；shell 不可用或读失败给 null。 */
        private fun rootRead(command: String): String? = try {
            val result = RootShell.control.exec(command)
            result.output.takeIf { result.exitCode == 0 && !result.timedOut }
        } catch (_: Exception) {
            null
        }
    }
}
