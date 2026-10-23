package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.clock.TimeOfDay
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.xboot.stdcall.posix
import java.io.File
import java.util.Calendar

/**
 * 重布设备定时开关机（/dev/McuCom，ioctl 0xA1，见 docs/device-poweroff.md §11）。
 *
 * 断电/上电目标是设备表盘上的 HH:MM，这里用本机钟换算成相对延时（分钟）写给 MCU：
 * 关机延时从命令落地起算，开机延时从断电时刻起算；目标时刻已过就落到次日（"下一个"）。
 * 截止判定用实时钟——钟被拨到越过截止即当场断电，回拨则等量推迟。一次布防覆盖上一次。
 *
 * 换算与浴室 App 自己的 `DataforHandle` 同一算法（反编译逐字移植），含它的字节序：
 * 载荷前两字节是开机延时、后两字节是关机延时，秒被截断（分钟粒度）。enable 固定
 * 3 = 关机与开机都使能。
 *
 * **不能替换成读回校验**：驱动未实现 0xA6（查询），写没写进去只能靠行为验证——
 * 到点断电、服务端离线事件。
 */
object McuPowerSchedule {

    private const val TAG = "McuPowerSchedule"

    /** 每天 24 小时换算成分钟，用于把"已过的时刻"滚到次日。 */
    private const val MINUTES_PER_DAY = 24 * 60

    /** /dev/McuCom 的打开参数：O_RDWR + 0666（对所有进程可写）。 */
    private const val O_RDWR = 3
    private const val MCU_MODE = 438

    /** 使能位 3 = 关机与开机都使能（浴室 App 的 DataforHandle 也固定写这个值）。 */
    private const val ENABLE_BOTH = 3

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 重布定时开关机：[offAt] 断电时刻、[onAt] 上电时刻，都是设备表盘上的 HH:MM。
     * 返回驱动是否收下（0）；格式不符或打不开设备都算失败，结果落 device.log。
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

        // 截止已过就滚到次日："下一个"语义。开机延时从断电时刻起算，同样按"下一个"算。
        val offMinutes = delayMinutes(nowMinuteOfDay, offMinuteOfDay)
        val offMomentMinuteOfDay = (nowMinuteOfDay + offMinutes) % MINUTES_PER_DAY
        val onMinutes = delayMinutes(offMomentMinuteOfDay, onMinuteOfDay)

        deviceLog.lifecycle(
            "power.schedule-started",
            "off=$offAt(${offMinutes}m) on=$onAt(+${onMinutes}m after off)",
        )
        val fd = posix.open("/dev/McuCom", O_RDWR, MCU_MODE)
        if (fd < 0) {
            Log.e(TAG, "cannot open /dev/McuCom: fd=$fd")
            deviceLog.error("power.schedule-failed", "打不开 /dev/McuCom")
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
     * 从 [nowMinuteOfDay] 到 [targetMinuteOfDay] 的相对分钟数；目标已过就滚到次日。
     * 秒在这里被截断——MCU 只吃分钟，截断只会让断电略早于目标，方向安全。
     */
    internal fun delayMinutes(nowMinuteOfDay: Int, targetMinuteOfDay: Int): Int =
        (targetMinuteOfDay - nowMinuteOfDay + MINUTES_PER_DAY) % MINUTES_PER_DAY

    /** "HH:MM" → 当天第几分钟；格式不符给 null。解析规则见 [TimeOfDay]。 */
    internal fun parseMinuteOfDay(text: String): Int? = TimeOfDay.parseMinuteOfDay(text)
}
