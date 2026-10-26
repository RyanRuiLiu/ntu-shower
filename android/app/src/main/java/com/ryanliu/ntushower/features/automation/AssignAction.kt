// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.transport.ReasonCodes

/**
 * 分配动作：HOME 页选中指定喷头 → 点击"无卡模式"按钮。
 *
 * 点击节点返回 false 时重新读一次页面/节点再点一次。结算刚结束、页面换页或节点被
 * 重建时，`performAction(ACTION_CLICK)` 会返回 false；这时节点还在，只是这一份不是
 * 可点击的那一份。只重试一次，避免把真正的界面异常拖成循环。
 */
class AssignAction(override val certNo: String, override val password: String, private val doorNo: Int) :
    AutomationFlow.Action {

    override val traceLabel = "assign door=$doorNo"

    override fun perform(a11y: A11yNodeSource, trace: AutomationTrace.Trace?): FlowError? {
        var door = BathAppNavigator.findDoor(a11y, doorNo)
            ?: return FlowError(ReasonCodes.DOOR_NOT_FOUND, "喷头 $doorNo 不存在")
        trace?.mark("find-door")
        // 标记取在点击**之前**：点完才取的话，这一下引起的变化会落在标记之外，
        // 于是又退回"等满固定间隔"——那正是这套标记要消掉的白等
        val mark = A11yRuntime.pageChangeMark()
        var awaitMark = mark
        var clicked = a11y.performClick(door)
        door.recycle()
        if (!clicked) {
            trace?.mark("retry-door-click")
            A11yRuntime.awaitPageChangeAfter(mark, AutomationFlow.uiSettleMs())
            BathAppPage.refresh()
            door = BathAppNavigator.findDoor(a11y, doorNo)
                ?: return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "点击喷头失败（重试时找不到喷头 $doorNo）")
            awaitMark = A11yRuntime.pageChangeMark()
            clicked = a11y.performClick(door)
            door.recycle()
        }
        if (!clicked) {
            return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "点击喷头失败")
        }
        trace?.mark("click-door")
        A11yRuntime.awaitPageChangeAfter(awaitMark, AutomationFlow.uiSettleMs())
        trace?.mark("await-door-selected")
        val noCardMark = A11yRuntime.pageChangeMark()
        if (!BathAppNavigator.clickNoCardButton(a11y)) {
            trace?.mark("retry-nocard-click")
            A11yRuntime.awaitPageChangeAfter(noCardMark, AutomationFlow.uiSettleMs())
            if (!BathAppNavigator.clickNoCardButton(a11y)) {
                return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "无法点击无卡模式按钮")
            }
        }
        trace?.mark("click-nocard")
        return null
    }
}
