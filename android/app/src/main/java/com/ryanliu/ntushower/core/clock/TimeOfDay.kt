package com.ryanliu.ntushower.core.clock

/**
 * 一天内时刻 `HH:MM` 的唯一解析处。
 *
 * 服务端 `app/modules/config/spec.py` 的 `parse_time()` 是它的对侧。设备端有电源计划
 * 重布与屏幕提示时段两处消费，必须按同一个形状认；`McuPowerSchedule.parseMinuteOfDay`
 * 保留原入口，内部委托到这里。
 */
object TimeOfDay {

    private val pattern = Regex("""^(\d{2}):(\d{2})$""")

    /** `HH:MM` → 当天第几分钟（0..1439）；格式或范围不符给 null。 */
    fun parseMinuteOfDay(text: String): Int? {
        val match = pattern.find(text) ?: return null
        val hour = match.groupValues[1].toInt()
        val minute = match.groupValues[2].toInt()
        if (hour > 23 || minute > 59) return null
        return hour * 60 + minute
    }
}
