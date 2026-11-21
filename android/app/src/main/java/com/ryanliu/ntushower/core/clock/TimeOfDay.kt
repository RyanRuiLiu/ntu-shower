package com.ryanliu.ntushower.core.clock

/**
 * 一天内时刻 `HH:MM` 的唯一解析处。
 *
 * 服务端 `app/modules/config/spec.py` 的 `parse_time()` 是它的对侧。设备端两处消费
 * （McuPowerSchedule 的电源计划换算、ConditionRule 的屏幕提示时段）必须按同一个形状
 * 认时刻；`McuPowerSchedule.parseMinuteOfDay` 保留原入口，内部委托到这里。严格四位
 * `HH:MM`：不带秒、不允许 `H:MM` 的宽松形态——它承载的是配置下发与设备上报两端的
 * 约定形状，宽进会在两端之间养出第三种写法。
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
