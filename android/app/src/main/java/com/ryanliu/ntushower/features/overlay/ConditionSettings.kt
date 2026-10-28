package com.ryanliu.ntushower.features.overlay

/**
 * 屏幕浮层的共用显示条件：时间窗与本地占用数。
 *
 * 屏幕提示与楼层状态行各自持有一份，值来自各自的配置键；判定逻辑只在
 * [ConditionRule] 一处。把值分开是为了两个窗口仍能独立显隐——共用的是语义，
 * 不是同一组开关。
 */
data class ConditionSettings(
    val timeEnabled: Boolean,
    val timeStart: String,
    val timeEnd: String,
    val weekdays: String,
    val occupancyEnabled: Boolean,
    val occupancyMin: Int,
    val occupancyMax: Int,
)

/** 一次条件判定需要的全部输入；由控制器在主线程收集。 */
data class ConditionInputs(val moment: LocalMoment?, val occupied: Int?, val coverVisible: Boolean)

/** 判定结果：显示，或隐藏并给出一个可记录的原因。 */
data class ConditionDecision(val visible: Boolean, val reason: String)
