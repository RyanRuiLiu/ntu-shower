package com.ryanliu.ntushower.service

import android.content.res.Resources
import android.util.Log
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.shell.RootShell

/**
 * 界面操作的 root shell 实现：点击、滑动、滚动、按键、输入。
 *
 * ## 什么时候用这一条
 *
 * 与无障碍通道（[A11yUiChannel]）各自完整，由 [UiChannel] 按**无障碍是否可用**
 * 选路：无障碍在时优先用它（正规接口、命中节点、不 spawn 进程），不在时这些
 * 操作经由本通道仍然能做到。真机上出现过无障碍**整体**失效（总开关被系统置 0
 * 且拒绝写回，恢复办法见 `com.ryanliu.ntushower.features.selfmanage.A11yGuard`），
 * 那时若没有这一条，远程就完全失去了操作设备界面的手段。
 *
 * ## 命令在本 ROM 上的实测结论
 *
 * DS83X / Android 4.4.4。判据是**界面是否真的变了**（截图或节点树做前后比对），
 * 不是命令的退出码——早年按退出码判断得出过"`input keyevent` 无效"的错误结论，
 * 原因是在不可滚动的界面上取证据，不是命令本身的问题。
 *
 * | 命令 | 结论 | 判据 |
 * |---|---|---|
 * | `input tap` | 可用 | 设置界面节点树前后 DIFF |
 * | `input swipe` | 可用 | 同上 |
 * | `input roll` | 可用 | 同上 |
 * | `input keyevent` | 可用 | 前台 Activity 由设置变为桌面 |
 * | `input text` | 可用（仅 ASCII） | 拨号盘 `digits` 节点文本变为写入值 |
 *
 * `input text` 按字节处理，**非 ASCII 会被写坏**——中文必须走无障碍的剪贴板
 * 粘贴（API 19 没有 `ACTION_SET_TEXT`）。
 */
object ShellUiChannel {

    private const val TAG = "ShellUiChannel"

    private val DURATION = Regex("""\d+""")

    /**
     * 滚动一次的滑动幅度，取屏幕高度的这个比例。
     *
     * 太小不足以触发滚动；太大在带惯性的列表上会变成抛掷、一次冲过好几屏。
     */
    private const val SCROLL_SPAN_RATIO = 0.4

    /** 点击。 */
    fun tap(x: Int, y: Int): Boolean = run("input tap $x $y", "tap")

    /**
     * 从 `(fromX, fromY)` 滑到 `(toX, toY)`，`durationMs` 是按下到抬起的时长。
     *
     * 显式给时长：`input swipe` 不传时长时用一个很短的值，会被识别成快速抛掷
     * 而非拖动。
     */
    fun swipe(fromX: Int, fromY: Int, toX: Int, toY: Int, durationMs: Int): Boolean {
        // 时长必须是正整数：`input swipe` 收到非数字会整条命令失败，
        // 而失败信息只说明"用法错误"，不指出是哪个参数
        if (!isPositive(durationMs)) {
            Log.w(TAG, "invalid swipe duration: $durationMs")
            return false
        }
        return run("input swipe $fromX $fromY $toX $toY $durationMs", "swipe")
    }

    /**
     * 滚动：以一次竖直滑动等效。
     *
     * `forward` 是"往列表后面滚"，视觉上内容上移，对应手指**向上**滑
     * （起点 y 更大）；反向则相反。
     */
    fun scroll(forward: Boolean): Boolean {
        val metrics = Resources.getSystem().displayMetrics
        val centerX = metrics.widthPixels / 2
        val centerY = metrics.heightPixels / 2
        val halfSpan = (metrics.heightPixels * SCROLL_SPAN_RATIO / 2).toInt()
        val duration = RuntimeConfig.uiSwipeDurationMs.toInt()
        return if (forward) {
            swipe(centerX, centerY + halfSpan, centerX, centerY - halfSpan, duration)
        } else {
            swipe(centerX, centerY - halfSpan, centerX, centerY + halfSpan, duration)
        }
    }

    /** 系统按键。 */
    fun key(key: UiKey): Boolean = run("input keyevent ${key.keyCode}", "key ${key.name}")

    /**
     * 输入文本。
     *
     * 只接受 ASCII：`input text` 在 4.4 上按字节处理，非 ASCII 会写坏。不满足时
     * 返回 false 让调用方知道该换通道，而不是发一条注定写乱内容的命令。
     */
    fun text(value: String): Boolean {
        if (!ShellTextCodec.isAsciiSafe(value)) {
            Log.w(TAG, "text is not ascii-safe, refusing input text")
            return false
        }
        return run("input text ${ShellTextCodec.encode(value)}", "text")
    }

    private fun run(command: String, label: String): Boolean {
        val result = RootShell.control.exec(command)
        if (!result.success) Log.w(TAG, "$label failed: ${result.output.take(120)}")
        return result.success
    }

    private fun isPositive(value: Int): Boolean = value > 0 && DURATION.matches(value.toString())
}
