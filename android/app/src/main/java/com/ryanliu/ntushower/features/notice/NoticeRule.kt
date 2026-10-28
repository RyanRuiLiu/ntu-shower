package com.ryanliu.ntushower.features.notice

import com.ryanliu.ntushower.features.overlay.ConditionDecision
import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionRule
import com.ryanliu.ntushower.features.overlay.ConditionSettings
import com.ryanliu.ntushower.features.overlay.OverlayReason

/** 一次判定的全部配置输入；由控制器在主线程从 RuntimeConfig 复制。 */
data class NoticeSettings(
    val enabled: Boolean,
    val text: String,
    val textSizeSp: Int,
    val textColor: String,
    val align: String,
    val centerOffsetDp: Int,
    val xDp: Int,
    val yDp: Int,
    val condition: ConditionSettings,
)

/** 屏幕提示特有的隐藏原因；时间/占用/遮盖的原因在 [OverlayReason]。 */
object NoticeReason {
    const val EMPTY_TEXT = "empty_text"
}

/**
 * 屏幕提示的唯一判定点。
 *
 * 提示特有的两条（开关、空文字）在这里；时间窗、占用数与遮盖层交给
 * [ConditionRule]——与楼层状态行是同一套语义。输入全部是纯数据，这里不读时钟、
 * 不读数据库、不碰 Android 视图。
 */
object NoticeRule {

    fun decide(settings: NoticeSettings, inputs: ConditionInputs): ConditionDecision {
        if (!settings.enabled) return ConditionDecision(false, OverlayReason.DISABLED)
        if (settings.text.trim().isEmpty()) return ConditionDecision(false, NoticeReason.EMPTY_TEXT)
        return ConditionRule.decide(settings.condition, inputs)
    }
}
