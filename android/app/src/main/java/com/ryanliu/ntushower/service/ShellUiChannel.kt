package com.ryanliu.ntushower.service

import android.content.res.Resources
import android.util.Log
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.shell.RootShell

/**
 * 界面操作的 root shell 实现：点击、滑动、滚动、按键。
 *
 * ## 什么时候走这一条
 *
 * 界面检视的这几样**只有这一条通道**：它们是坐标语义，而远程操作的人本来就是指着截图上的
 * 位置点下去的——坐标天然对应，不必绕到节点树上。无障碍那侧只保留文本输入
 * （[A11yUiChannel]），因为中文必须走剪贴板粘贴。分工见
 * `com.ryanliu.ntushower.features.automation.CommandScheduler` 的 `executeUiAction`。
 *
 * 真机上出现过无障碍**整体**失效（总开关被系统置 0 且拒绝写回，恢复办法见
 * `com.ryanliu.ntushower.features.selfmanage.A11yGuard`）：那时这几项照常工作，是远程
 * 唯一还能碰到设备界面的手段。
 *
 * ## 命令在本 ROM 上的实测结论
 *
 * DS83X / Android 4.4.4。下表第三列的"判据"指**当初怎么验证**这条命令真的生效——看界面
 * 是否真的变了（截图或节点树前后比对），而不是看命令的退出码。早年按退出码判断得出过
 * "`input keyevent` 无效"的错误结论，原因是在不可滚动的界面上取证据，不是命令本身的问题。
 *
 * 这与 [run] 用退出码判成败不是一回事：那里判的是"命令执行了没有"。
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
 * 粘贴（API 19 没有 `ACTION_SET_TEXT`）。因此本通道**不提供**文本输入：它只走得通
 * ASCII，而要输的账号密码是纯数字、中文提示走不到这里，一条只能输 ASCII 的通路
 * 没有用武之地。真需要时再按上表那一行的结论接回来即可。
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
     * 执行一条 input 命令。
     *
     * 判的是**命令有没有执行成功**（shell 退出码），不是"界面是否因此变了"——后者要再做
     * 一次节点树或截图比对，成本高一个量级，而调用方要的只是"这条命令发出去了且没报错"。
     * 两者为什么不能用同一个判据，见类注释那张实测表。
     */
    private fun run(command: String, label: String): Boolean {
        val result = RootShell.control.exec(command)
        val ok = result.exitCode == 0
        if (!ok) Log.w(TAG, "$label failed: ${result.output.take(120)}")
        return ok
    }

    private fun isPositive(value: Int): Boolean = value > 0 && DURATION.matches(value.toString())
}
