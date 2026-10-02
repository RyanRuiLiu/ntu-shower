package com.ryanliu.ntushower.features.automation

import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.transport.ReasonCodes

/** 结算动作：HOME 页点击结算按钮。 */
class SettleAction(override val certNo: String, override val password: String) : AutomationFlow.Action {

    override val traceLabel = "settle"

    override fun perform(a11y: A11yNodeSource, trace: AutomationTrace.Trace?): FlowError? {
        if (!BathAppNavigator.clickSettleButton(a11y)) {
            return FlowError(ReasonCodes.UI_CLICK_NO_EFFECT, "无法点击结算按钮")
        }
        trace?.mark("click-settle")
        return null
    }
}
