package com.ryanliu.ntushower.features.selfmanage

import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.shell.RootShell
import java.util.TimeZone

/**
 * 设备本地事实：浴室 App 从 V8 平台拿到、并写进自己 SharedPreferences 的每日开关机
 * 时刻、出水与占用阈值，与本机时区偏移。两组语义住同一个文件（浴室 App 的
 * `data.xml`），一次 root 读取全部解析。
 *
 * - **电源计划**（autoCloseTime/autoOpenTime/时区）：服务端的 hold 对账、MCU 常规重布
 *   与语音的当地时刻按它换算——时刻由设备侧自报，服务端不定任何时刻。来源是
 *   `<string name="autoopentime">10:55</string>` 与 `autoclosetime`。这与 App 启动时
 *   装载、`WelcomeActivity:716` 交给 smdt 布防的是同一份值；`/sdcard/datalook/log/`
 *   下的 `Params_*.txt` 只是下载日志的副本，还会被 `logExpiredDate` 清掉，不作为来源。
 * - **出水与占用阈值**（maxBatchTimeMin/maxInSpaceTimeMin/maxBatchSpaceTimeMin，分钟）：
 *   服务端的段长上限、盲区划断与续期阈值按它取值——同一参数在各楼配置不同
 *   （2026-11-23 生产体检：六楼 30 分钟、其余 20 分钟），写死在服务端会错。
 *   时刻键与阈值键在文件里形态不同：前者是文本形态（`>10:55</string>`），后者是
 *   属性形态（`value="20"`），解析各按各的来。
 *
 * 文件属 App 私有目录且本进程读不到：读取经 root shell（与 `WaterTargetReader`
 * 读 waterList、`DoorSnapshotReader` 读 bath.db 同一前提）。键缺失如实给 null：
 * 服务端按「计划/阈值未知」处理，回退到既有默认，不发明值。
 */
data class BathAppFacts(
    val autoCloseTime: String,
    val autoOpenTime: String,
    val zoneOffsetMinutes: Int,
    val maxBatchTimeMin: Int,
    val maxInSpaceTimeMin: Int,
    val maxBatchSpaceTimeMin: Int,
) {
    /** 上报载荷：device.status 帧的 payload。 */
    fun toPayload(): Map<String, Any> = mapOf(
        "auto_close_time" to autoCloseTime,
        "auto_open_time" to autoOpenTime,
        "zone_offset_minutes" to zoneOffsetMinutes,
        "max_batch_time_min" to maxBatchTimeMin,
        "max_in_space_time_min" to maxInSpaceTimeMin,
        "max_batch_space_time_min" to maxBatchSpaceTimeMin,
    )

    /** 内容指纹：变化检测用（与 DoorSnapshotSender.lastHash 同一手法）。 */
    fun fingerprint(): Int = listOf(
        autoCloseTime,
        autoOpenTime,
        zoneOffsetMinutes,
        maxBatchTimeMin,
        maxInSpaceTimeMin,
        maxBatchSpaceTimeMin,
    ).hashCode()

    companion object {
        /** `data.xml` 里的两组键：时刻是文本形态，阈值是属性形态（2026-11-23 现场实测）。 */
        private val AUTO_CLOSE = Regex("""<string name="autoclosetime">(\d{2}:\d{2})</string>""")
        private val AUTO_OPEN = Regex("""<string name="autoopentime">(\d{2}:\d{2})</string>""")
        private val MAX_BATCH = Regex("""<int name="maxbatchtime" value="(\d+)"\s*/>""")
        private val MAX_IN_SPACE = Regex("""<int name="maxinspacetime" value="(\d+)"\s*/>""")
        private val MAX_BATCH_SPACE = Regex("""<int name="maxbatchspacetime" value="(\d+)"\s*/>""")

        /** 读 App 的 `data.xml` 并解析全部键。
         *  [readFile] 是注入的 root 读取函数：生产绑定 RootShell，测试注入纯函数。 */
        fun read(
            paramsFile: String = AppConfig.BATH_PREFS_PATH,
            readFile: (String) -> String? = ::rootRead,
        ): BathAppFacts? {
            val text = readFile(paramsFile) ?: return null
            val close = AUTO_CLOSE.find(text)?.groupValues?.get(1) ?: return null
            val open = AUTO_OPEN.find(text)?.groupValues?.get(1) ?: return null
            val batch = MAX_BATCH.find(text)?.groupValues?.get(1)?.toInt() ?: return null
            val inSpace = MAX_IN_SPACE.find(text)?.groupValues?.get(1)?.toInt() ?: return null
            val batchSpace = MAX_BATCH_SPACE.find(text)?.groupValues?.get(1)?.toInt() ?: return null
            val zone = TimeZone.getDefault().rawOffset / 60_000
            return BathAppFacts(
                autoCloseTime = close,
                autoOpenTime = open,
                zoneOffsetMinutes = zone,
                maxBatchTimeMin = batch,
                maxInSpaceTimeMin = inSpace,
                maxBatchSpaceTimeMin = batchSpace,
            )
        }

        /**
         * 读文件的 shell 命令：路径是常量，单引号包裹防空格与元字符。
         *
         * 必须是 `cat <路径>`，不能把路径本身下发——root shell 会把它当命令执行，
         * 一个 `.xml` 只会得到 exit≠0，facts 就永远缺报（0.8.1 的实缺陷）。
         */
        internal fun readCommand(path: String): String = "cat '" + path.replace("'", "'\\''") + "'"

        /** 经 root shell 读文件全文；shell 不可用或读失败给 null。 */
        private fun rootRead(path: String): String? = try {
            val result = RootShell.control.exec(readCommand(path))
            result.output.takeIf { result.exitCode == 0 && !result.timedOut }
        } catch (_: Exception) {
            null
        }
    }
}
