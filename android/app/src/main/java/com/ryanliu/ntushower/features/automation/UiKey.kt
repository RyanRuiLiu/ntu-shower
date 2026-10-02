package com.ryanliu.ntushower.features.automation

/**
 * 系统按键：远程界面操作里的导航键与方向键。
 *
 * 只有 shell 一条通道（`input keyevent`），[keyCode] 就是它的数字码。无障碍侧不做按键
 * ——那里曾经有过 `performGlobalAction`，界面检视的写操作统一走 shell 之后撤掉了
 * （理由见 [A11yUiChannel] 的类注释：那些操作本来就是坐标语义）。
 *
 * **[RECENTS] 在本 ROM 上实测无效**：`input keyevent 187` 返回成功但界面不变。保留它是因为
 * "有哪些按键"是跨端契约的一部分（服务端的 `DeviceUiKeyRequest` 与网页端都按这套取值），
 * 移除要三处一起改，而它作为选项本身无害。真要 recent 界面得另找通路。
 *
 * 方向键组（[DPAD_UP] 等）与 [ENTER]、[DEL] 服务于焦点导航：▲▼◀▶ 在控件间移动焦点
 * 光标、[DPAD_CENTER] 按下焦点控件——对照旧项目 viewer 按钮栏的同名按键（同样走
 * `input keyevent`）。它们也是逐字符删除与控件间导航的主要手段。
 *
 * 音量与电源不在这里：那类按键需要实体按键事件注入，不在本项目的能力集内。
 */
enum class UiKey(val keyCode: Int) {
    BACK(4),
    HOME(3),
    RECENTS(187),
    NOTIFICATIONS(83),
    DPAD_UP(19),
    DPAD_DOWN(20),
    DPAD_LEFT(21),
    DPAD_RIGHT(22),
    DPAD_CENTER(23),
    ENTER(66),
    DEL(67),
    ;

    companion object {
        /** 解析协议里的按键名；无法识别返回 null（调用方据此报参数错误）。 */
        fun parse(raw: String): UiKey? = entries.firstOrNull { it.name.equals(raw, ignoreCase = true) }
    }
}
