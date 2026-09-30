// 设备 ROM 冻结在 Android 4.4（API 19）：本文件只做通道选择，不直接持有节点。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.service

import com.ryanliu.ntushower.core.config.RuntimeConfig

/**
 * 界面操作的统一入口：按**无障碍是否可用**在两条通道间选路。
 *
 * ## 两条通道，各自完整
 *
 * | 通道 | 实现 | 特点 |
 * |---|---|---|
 * | 无障碍 | [A11yUiChannel] | Android 正规接口，按节点操作，不 spawn 进程 |
 * | root shell | [ShellUiChannel] | 走 `input` 命令，不需要无障碍在运行 |
 *
 * 二者不是主备关系：每种操作两边都能做完，选哪条只取决于无障碍当前是否可用。
 * 无障碍在时优先用它——它命中节点而非坐标，且不额外起进程。
 *
 * ## 为什么必须留着 shell 那条
 *
 * 真机上出现过无障碍**整体**失效：总开关被系统置 0 且拒绝写回（恢复办法见
 * `A11yGuard` 的类注释）。那段时间所有 `ui.*` 命令都返回 `no_a11y`，远程
 * **完全**失去了操作设备界面的手段——连"点一下、按个返回"都做不到。shell 那条
 * 在无障碍不在时照常工作，是这种局面下唯一还能碰到设备界面的方式。
 *
 * ## 例外：节点树没有第二条通道
 *
 * shell 侧的 `uiautomator dump` 会经 `UiAutomation` 接管系统无障碍会话，真机上
 * 出现过用它取过几次节点后整机无障碍失效的情况（因果未坐实，但风险远大于便利），
 * 因此不采用。无障碍不在时 `ui.dump` 就是不可用——如实失败，不给一个更差的替代。
 *
 * ## 定位方式
 *
 * 目前只按屏幕坐标。元素定位应交给 UiSelector 风格的选择器
 * （`resource-id` / `text` / `class`，可组合）——真机已证明
 * `resource-id`（如 `com.datalook.bath:id/ll_maintitle`）唯一且稳定。
 * 选择器尚未实现，因此暂不提供；不用"树的遍历序号"是因为它随界面变化错位，
 * 真机上已因此点错地方。
 */
object UiChannel {

    /** 按键：无障碍不在时走 shell 的 `input keyevent`。 */
    fun key(service: A11yService?, key: UiKey): Boolean =
        if (service == null) ShellUiChannel.key(key) else A11yUiChannel.sendKey(service, key)

    /**
     * 向当前焦点控件输入文本。
     *
     * 无障碍在时用剪贴板 + `ACTION_PASTE`——API 19 没有 `ACTION_SET_TEXT`，这是该
     * API 级别上唯一能写入任意字符的方式。不在时走 `input text`，它只吃 ASCII，
     * 非 ASCII 由 [ShellUiChannel.text] 拒绝，命令如实失败而不是写乱内容。
     */
    fun input(service: A11yService?, text: String): Boolean =
        if (service == null) ShellUiChannel.text(text) else A11yUiChannel.typeIntoFocus(service, text)

    /** 按屏幕坐标点击：无障碍取该点下最深且可点击的元素，不在时直接注入触摸事件。 */
    fun tap(service: A11yService?, x: Int, y: Int): Boolean =
        if (service == null) ShellUiChannel.tap(x, y) else A11yUiChannel.clickAt(service, x, y)

    /**
     * 滑动。
     *
     * 一直是 shell 实现：无障碍的 `dispatchGesture` 要 API 24，本设备是 19，
     * **做不到合成任意坐标手势**——这不是取舍，是运行时下限。
     *
     * 时长取 `RuntimeConfig.uiSwipeDurationMs`（可调）：过短会被识别成快速抛掷。
     */
    fun swipe(fromX: Int, fromY: Int, toX: Int, toY: Int, durationMs: Int = 0): Boolean =
        ShellUiChannel.swipe(fromX, fromY, toX, toY, effectiveDuration(durationMs))

    /**
     * 滚动可滚动容器。
     *
     * 与 [swipe] 分开：这条作用于**容器**而不是坐标，语义不同。无障碍在时用
     * `ACTION_SCROLL_FORWARD`／`ACTION_SCROLL_BACKWARD`（API 16+），不在时用一次
     * 竖直滑动等效。
     */
    fun scroll(service: A11yService?, forward: Boolean): Boolean =
        if (service == null) ShellUiChannel.scroll(forward) else A11yUiChannel.scroll(service, forward)

    /**
     * 导出节点树，返回 `uiautomator dump` 同格式的 XML。
     *
     * 节点上限取 `RuntimeConfig.uiDumpMaxNodes`（可调）——它同时决定回传体积。
     * 无障碍不在时返回 null（没有第二条通道，理由见类注释）。
     */
    fun dump(service: A11yService?): String? =
        if (service == null) null else UiTreeCodec.export(service, RuntimeConfig.uiDumpMaxNodes.toInt())

    /** 未指定时长时取配置的默认值；显式给了就用给的。 */
    private fun effectiveDuration(requested: Int): Int =
        if (requested > 0) requested else RuntimeConfig.uiSwipeDurationMs.toInt()
}
