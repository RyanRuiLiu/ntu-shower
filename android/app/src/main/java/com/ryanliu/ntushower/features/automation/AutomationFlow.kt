package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.CoverOverlay
import com.ryanliu.ntushower.service.A11yService

/** 流程失败：reason_code 结构化错误码 + 人读描述。 */
data class FlowError(val reasonCode: String, val message: String)

/**
 * 分配/结算共用流程模板（8 步，真机验证过的顺序）：
 *
 * 1 回首页 → 2 遮盖层 → 3 命令动作（可插拔）→ 4 等验证页
 * → 5 输入凭证 → 6 等浴室服务器响应（终点页捕获）→ 7 等回首页 → 8 撤遮盖+清选中态
 *
 * 步骤 3 由具体动作（AssignAction/SettleAction）提供；
 * 失败返回 [FlowError]（reason_code 为结构化协议字段）。
 */
object AutomationFlow {

    interface Action {
        /** 浴室 App 验证页所需凭证（由命令参数下发）。 */
        val certNo: String
        val password: String

        /** 执行命令专属 UI 动作；返回 null 表示成功，返回 [FlowError] 表示失败。 */
        fun perform(a11y: A11yService): FlowError?
    }

    fun run(a11y: A11yService, action: Action, overlay: CoverOverlay): FlowOutcome {
        // 1 回首页
        if (!BathAppNavigator.navigateToHome(a11y, RuntimeConfig.homeBudgetMs)) {
            return FlowOutcome(error = FlowError("automation.navigate_failed", "无法回到首页"))
        }
        // 2 遮盖层（可经配置关闭；文案取运行时配置）
        if (RuntimeConfig.overlayEnabled) overlay.show(RuntimeConfig.overlayMessage)
        try {
            // 3 命令动作
            action.perform(a11y)?.let {
                overlay.hide()
                return FlowOutcome(error = it)
            }

            // 4 等待验证页
            if (!BathAppNavigator.waitForPage(a11y, PageState.ID_VERIFY, RuntimeConfig.idVerifyWaitMs)) {
                overlay.hide()
                return FlowOutcome(error = FlowError("automation.verify_page_timeout", "无法进入验证页面"))
            }

            // 5 输入账号密码（凭证由命令参数下发）
            if (!BathAppNavigator.inputCredentialsAndConfirm(a11y, action.certNo, action.password)) {
                overlay.hide()
                return FlowOutcome(error = FlowError("automation.input_failed", "输入账号密码失败"))
            }

            // 6 等待浴室服务器响应（终点页：HOME 成功 / HINT·VF_RESULT 拒绝）
            val terminal = awaitTerminal(a11y)
            if (!terminal.reached) {
                overlay.hide()
                return FlowOutcome(error = FlowError("automation.response_timeout", terminal.message))
            }

            return FlowOutcome(terminalMessage = terminal.message)
        } catch (e: Exception) {
            try {
                overlay.hide()
            } catch (_: Exception) {}
            throw e
        } finally {
            Log.d(TAG, "flow finished")
        }
    }

    /** 流程收尾：等回首页 → 撤遮盖 → input tap 清除网格选中态。在结果回报之后调用。 */
    fun finish(a11y: A11yService, overlay: CoverOverlay) {
        try {
            BathAppNavigator.waitForPage(a11y, PageState.HOME, 10_000L)
        } finally {
            overlay.hide()
            val center = BathAppNavigator.getDateViewCenter(a11y)
            if (center != null) {
                RootShell.exec("input tap ${center.first} ${center.second}")
            }
        }
    }

    private data class Terminal(val reached: Boolean, val message: String = "")

    private fun awaitTerminal(a11y: A11yService): Terminal {
        val start = System.currentTimeMillis()
        var relaunched = false
        while (System.currentTimeMillis() - start < RuntimeConfig.serverResponseTimeoutMs) {
            val (state, text) = BathAppNavigator.detectPage()
            when (state) {
                PageState.HOME -> return Terminal(true)

                PageState.ID_VERIFY -> Unit

                // 继续等待
                PageState.HINT, PageState.VF_RESULT -> return Terminal(true, text)

                PageState.UNKNOWN, PageState.BOOKING, PageState.BOOKING_CODE -> {
                    if (!relaunched && !A11yService.bathAppInForeground) {
                        a11y.launchBathApp()
                        relaunched = true
                        A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
                        continue
                    }
                }
            }
            A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
        }
        BathAppNavigator.navigateToHome(a11y, RuntimeConfig.homeBudgetMs)
        return Terminal(false, "服务器响应超时")
    }

    /**
     * 流程结果。
     *
     * 不叫 Result：`kotlin.Result` 是内置类型，同名会在导入场景下互相遮蔽。
     */
    data class FlowOutcome(val error: FlowError? = null, val terminalMessage: String = "")

    private const val TAG = "AutomationFlow"
}
