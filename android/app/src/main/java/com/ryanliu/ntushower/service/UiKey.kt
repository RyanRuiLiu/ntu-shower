package com.ryanliu.ntushower.service

/**
 * 系统按键。
 *
 * 两条通道各自有原生表示：无障碍用 `performGlobalAction` 的常量，
 * shell 用 `input keyevent` 的数字码。两者在此对齐，调用方只认这个枚举。
 *
 * 只包含**无障碍服务能处理的全局动作**。音量、电源不属于它的职责范围，
 * 那类按键需要实体按键事件注入，不在本项目的能力集内。
 */
enum class UiKey(val keyCode: Int) {
    BACK(4),
    HOME(3),
    RECENTS(187),
    NOTIFICATIONS(83),
    ;

    companion object {
        /** 解析协议里的按键名；无法识别返回 null（调用方据此报参数错误）。 */
        fun parse(raw: String): UiKey? = entries.firstOrNull { it.name.equals(raw, ignoreCase = true) }
    }
}
