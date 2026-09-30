package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.service.A11yService

/** 结算动作：HOME 页点击结算按钮。 */
class SettleAction(override val certNo: String, override val password: String) : AutomationFlow.Action {

    override val traceLabel = "settle"

    override fun perform(a11y: A11yService): FlowError? {
        if (!BathAppNavigator.clickSettleButton(a11y)) {
            return FlowError("ui.click_no_effect", "无法点击结算按钮")
        }
        return null
    }
}
