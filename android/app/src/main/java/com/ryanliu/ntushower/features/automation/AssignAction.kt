// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.transport.ReasonCodes

/** 分配动作：HOME 页选中指定喷头 → 点击"无卡模式"按钮。 */
class AssignAction(override val certNo: String, override val password: String, private val doorNo: Int) :
    AutomationFlow.Action {

    override val traceLabel = "assign door=$doorNo"

    override fun perform(a11y: A11yNodeSource, trace: AutomationTrace.Trace?): FlowError? {
        val door = BathAppNavigator.findDoor(a11y, doorNo)
            ?: return FlowError(ReasonCodes.DOOR_NOT_FOUND, "喷头 $doorNo 不存在")
        trace?.mark("find-door")
        // 标记取在点击**之前**：点完才取的话，这一下引起的变化会落在标记之外，
        // 于是又退回"等满固定间隔"——那正是这套标记要消掉的白等
        val mark = A11yRuntime.pageChangeMark()
        try {
            if (!a11y.performClick(door)) {
                return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "点击喷头失败")
            }
        } finally {
            door.recycle()
        }
        trace?.mark("click-door")
        A11yRuntime.awaitPageChangeAfter(mark, RuntimeConfig.uiClickDelayMs)
        trace?.mark("await-door-selected")
        if (!BathAppNavigator.clickNoCardButton(a11y)) {
            return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "无法点击无卡模式按钮")
        }
        trace?.mark("click-nocard")
        return null
    }
}
