package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.service.A11yService

/** 分配动作：HOME 页选中指定喷头 → 点击"无卡模式"按钮。 */
class AssignAction(override val certNo: String, override val password: String, private val doorNo: Int) :
    AutomationFlow.Action {

    override fun perform(a11y: A11yService): FlowError? {
        val door = BathAppNavigator.findDoor(a11y, doorNo)
            ?: return FlowError("automation.door_not_found", "喷头 $doorNo 不存在")
        try {
            if (!a11y.performClick(door)) {
                return FlowError("automation.click_failed", "点击喷头失败")
            }
        } finally {
            door.recycle()
        }
        A11yService.awaitPageChange(RuntimeConfig.uiClickDelayMs)
        if (!BathAppNavigator.clickNoCardButton(a11y)) {
            return FlowError("automation.click_failed", "无法点击无卡模式按钮")
        }
        return null
    }
}
