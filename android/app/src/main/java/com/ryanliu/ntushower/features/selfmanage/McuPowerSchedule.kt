package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.clock.TimeOfDay
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.xboot.stdcall.posix
import java.io.File
import java.util.Calendar

/**
 * 重布设备定时开关机（/dev/McuCom，ioctl 0xA1，语义依据见 docs/device-poweroff.md，
 * 受控实验定论在它的 2026-11-21 实验节）。
 *
 * 断电/上电目标是设备表盘上的 HH:MM，本对象用本机钟换算成相对延时（分钟）写入
 * MCU：关机延时自命令落地起算，开机延时自断电时刻起算；目标时刻已过即顺延至次日。
 * 截止判定使用实时钟——钟面被拨至越过截止即当场断电，回拨则等量推迟。
 *
 * ## MCU 写入语义（全部经实测钉死，写入方必须遵守）
 *
 * - **最后写入唯一生效**：更长与更短的截止均生效（实验节 A/B/D 三组验证）。整晚仅
 *   布防一次（ADR-0025），取消经重布更早截止实现。
 * - **0 延时没有立即语义**：写入后约 3-4 分钟才动作。[computeDelays] 在唯一写入
 *   入口拒绝它——延时为 0 只源于调用方误用，写入前拒绝优于写入后产生计划外的
 *   断电或上电。
 * - **enable 固定为 3**（关机与开机均使能，浴室 App 的 DataforHandle 亦写该值）：
 *   enable=0 会使清晨上电一并失效，常规夜的断电/上电循环两头落空，因此禁用。
 * - 延时粒度为分钟，秒被截断。
 *
 * 换算与浴室 App 的 `DataforHandle` 为同一算法（反编译逐字移植），含其字节序：
 * 载荷前两字节是开机延时、后两字节是关机延时。
 *
 * **不能以读回校验替代**：驱动未实现 0xA6（查询），写入是否生效只能经行为验证——
 * 到点断电与服务端离线事件。
 */
object McuPowerSchedule {

    private const val TAG = "McuPowerSchedule"

    /** 每天 24 小时换算成分钟，用于把"已过的时刻"顺延至次日。 */
    private const val MINUTES_PER_DAY = 24 * 60

    /** /dev/McuCom 的打开参数：O_RDWR + 0666（对所有进程可写）。 */
    private const val O_RDWR = 3
    private const val MCU_MODE = 438

    /** 使能位 3 = 关机与开机均使能（浴室 App 的 DataforHandle 亦固定写该值）。 */
    private const val ENABLE_BOTH = 3

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 重布定时开关机：[offAt] 为断电时刻、[onAt] 为上电时刻，均为设备表盘上的 HH:MM。
     * 返回驱动是否收下（0）；格式不符、延时为 0 或设备打开失败均视为失败，结果落 device.log。
     */
    fun schedule(offAt: String, onAt: String): Boolean {
        val offMinuteOfDay = parseMinuteOfDay(offAt)
        val onMinuteOfDay = parseMinuteOfDay(onAt)
        if (offMinuteOfDay == null || onMinuteOfDay == null) {
            Log.w(TAG, "rejected malformed time: off=$offAt on=$onAt")
            deviceLog.error("power.schedule-failed", "格式不符: off=$offAt on=$onAt")
            return false
        }
        val now = Calendar.getInstance()
        val nowMinuteOfDay = now.get(Calendar.HOUR_OF_DAY) * 60 + now.get(Calendar.MINUTE)
        val delays = computeDelays(nowMinuteOfDay, offMinuteOfDay, onMinuteOfDay)
        if (delays == null) {
            Log.w(TAG, "rejected zero delay: off=$offAt on=$onAt")
            deviceLog.error("power.schedule-failed", "延时为 0（目标与当前时刻同分钟）: off=$offAt on=$onAt")
            return false
        }
        val (offMinutes, onMinutes) = delays

        deviceLog.lifecycle(
            "power.schedule-started",
            "off=$offAt(${offMinutes}m) on=$onAt(+${onMinutes}m after off)",
        )
        val fd = posix.open("/dev/McuCom", O_RDWR, MCU_MODE)
        if (fd < 0) {
            Log.e(TAG, "cannot open /dev/McuCom: fd=$fd")
            deviceLog.error("power.schedule-failed", "无法打开 /dev/McuCom")
            return false
        }
        val written = try {
            posix.poweronoff(
                (onMinutes / 60).toByte(),
                (onMinutes % 60).toByte(),
                (offMinutes / 60).toByte(),
                (offMinutes % 60).toByte(),
                ENABLE_BOTH.toByte(),
                fd,
            ) == 0
        } finally {
            posix.close(fd)
        }
        if (written) {
            Log.i(TAG, "power schedule written: off=${offMinutes}m on=+${onMinutes}m after off")
            deviceLog.lifecycle("power.schedule", "off=${offMinutes}m on=+${onMinutes}m after off")
        } else {
            Log.e(TAG, "poweronoff ioctl failed")
            deviceLog.error("power.schedule-failed", "poweronoff 调用失败")
        }
        return written
    }

    /**
     * 换算一对相对延时（分钟）：关机延时自 [nowMinuteOfDay] 起算，开机延时自断电时刻
     * 起算，目标时刻已过即顺延至次日（"下一个"语义）。
     *
     * 任一延时为 0 时返回 null（语义见类注释）。生产路径的目标时刻均与当前时刻相差
     * 分钟以上：常规夜为 23:58/10:55，服务端取消路径为 now+90 秒；延时为 0 只源于
     * 调用方误用。
     */
    internal fun computeDelays(nowMinuteOfDay: Int, offMinuteOfDay: Int, onMinuteOfDay: Int): Pair<Int, Int>? {
        val offMinutes = delayMinutes(nowMinuteOfDay, offMinuteOfDay)
        if (offMinutes == 0) return null
        val offMomentMinuteOfDay = (nowMinuteOfDay + offMinutes) % MINUTES_PER_DAY
        val onMinutes = delayMinutes(offMomentMinuteOfDay, onMinuteOfDay)
        if (onMinutes == 0) return null
        return offMinutes to onMinutes
    }

    /**
     * 自 [nowMinuteOfDay] 至 [targetMinuteOfDay] 的相对分钟数；目标时刻已过即顺延至次日。
     * 秒在此处被截断——MCU 的延时粒度为分钟，截断仅使断电早于目标，失败方向安全。
     */
    internal fun delayMinutes(nowMinuteOfDay: Int, targetMinuteOfDay: Int): Int =
        (targetMinuteOfDay - nowMinuteOfDay + MINUTES_PER_DAY) % MINUTES_PER_DAY

    /** "HH:MM" → 当天第几分钟；格式不符返回 null。解析规则见 [TimeOfDay]。 */
    internal fun parseMinuteOfDay(text: String): Int? = TimeOfDay.parseMinuteOfDay(text)
}
