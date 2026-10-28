package com.ryanliu.ntushower.features.statusline

import com.ryanliu.ntushower.features.overlay.ConditionDecision
import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionRule
import com.ryanliu.ntushower.features.overlay.OverlayReason

/** 状态行特有的隐藏原因；时间/占用/遮盖的原因在 [OverlayReason]。 */
object StatusLineReason {
    const val NO_STATUS = "no_status"
    const val STALE = "stale"
}

/**
 * 楼层状态行的唯一判定点：先看这一行自己有没有可显示的数据，再交给共用条件。
 *
 * 顺序固定为：总开关 → 有没有收到状态 → 是否过期 → 时间/占用/遮盖。过期排在条件
 * 之前，是因为「服务端已经不推了」比「当前不在时段内」更值得记录；两者都隐藏时，
 * 日志应当指向前者。
 */
object StatusLineRule {

    fun decide(
        settings: StatusLineSettings,
        inputs: ConditionInputs,
        hasStatus: Boolean,
        stale: Boolean,
    ): ConditionDecision {
        if (!settings.enabled) return ConditionDecision(false, OverlayReason.DISABLED)
        if (!hasStatus || settings.floors.isEmpty()) return ConditionDecision(false, StatusLineReason.NO_STATUS)
        if (stale) return ConditionDecision(false, StatusLineReason.STALE)
        return ConditionRule.decide(settings.condition, inputs)
    }
}
