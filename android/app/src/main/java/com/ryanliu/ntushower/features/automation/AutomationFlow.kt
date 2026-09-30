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

        /** 耗时记录里用作区分的前缀，见 [AutomationTrace]。 */
        val traceLabel: String

        /** 执行命令专属 UI 动作；返回 null 表示成功，返回 [FlowError] 表示失败。 */
        fun perform(a11y: A11yService): FlowError?
    }

    fun run(a11y: A11yService, action: Action, overlay: CoverOverlay): FlowOutcome {
        // 开关关闭时 trace 为 null，各标记点退化成空操作——两条路径跑的是同一套代码
        val trace = AutomationTrace.begin(action.traceLabel)
        // 先让页面状态与当前界面同步一次：缓存只在无障碍事件到达时更新，而"上一次事件"
        // 可能发生在很久以前（比如流程之间的空档）。不刷新就开始判断，读到的可能是
        // 上一轮残留的状态。
        BathAppPage.refresh()
        trace?.mark("refresh-page")
        // 1 回首页
        if (!BathAppNavigator.navigateToHome(a11y, RuntimeConfig.homeBudgetMs)) {
            trace?.end("home_unreachable")
            return FlowOutcome(error = FlowError("device.home_unreachable", "无法回到首页"))
        }
        trace?.mark("navigate-home")
        // 2 遮盖层（可经配置关闭；文案取运行时配置）
        if (RuntimeConfig.overlayEnabled) overlay.show()
        trace?.mark("overlay-show")
        try {
            // 3 命令动作
            action.perform(a11y)?.let {
                overlay.hide()
                trace?.end("action_failed:${it.reasonCode}")
                return FlowOutcome(error = it)
            }
            trace?.mark("perform-action")

            // 4 等待验证页
            if (!BathAppNavigator.waitForPage(a11y, PageState.ID_VERIFY, RuntimeConfig.idVerifyWaitMs)) {
                overlay.hide()
                trace?.end("verify_page_timeout")
                return FlowOutcome(error = FlowError("device.verify_page_timeout", "无法进入验证页面"))
            }
            trace?.mark("wait-verify-page")

            // 5 输入账号密码（凭证由命令参数下发）
            if (!BathAppNavigator.inputCredentialsAndConfirm(a11y, action.certNo, action.password)) {
                overlay.hide()
                trace?.end("input_failed")
                return FlowOutcome(error = FlowError("device.login_input_failed", "输入账号密码失败"))
            }
            trace?.mark("input-credentials")

            // 6 等待浴室服务器响应（终点页：HOME 成功 / HINT·VF_RESULT 拒绝）
            val terminal = awaitTerminal(a11y)
            if (!terminal.reached) {
                overlay.hide()
                trace?.end("bath_server_timeout")
                return FlowOutcome(error = FlowError("device.bath_server_timeout", terminal.message))
            }
            trace?.mark("await-terminal")

            trace?.end("ok")
            return FlowOutcome(terminalMessage = terminal.message)
        } catch (e: Exception) {
            trace?.end("crashed:${e.message}")
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
                RootShell.control.exec("input tap ${center.first} ${center.second}")
            }
        }
    }

    private data class Terminal(val reached: Boolean, val message: String = "")

    private fun awaitTerminal(a11y: A11yService): Terminal {
        val start = System.currentTimeMillis()
        var relaunched = false
        var unknownRounds = 0
        while (System.currentTimeMillis() - start < RuntimeConfig.serverResponseTimeoutMs) {
            val mark = A11yService.pageChangeMark()
            val snapshot = BathAppPage.snapshot()
            when (snapshot.state) {
                PageState.HOME -> return Terminal(true)

                PageState.ID_VERIFY -> Unit

                // 继续等待
                PageState.HINT, PageState.VF_RESULT -> return Terminal(true, snapshot.message)

                PageState.UNKNOWN, PageState.BOOKING, PageState.BOOKING_CODE -> {
                    if (!relaunched && !A11yService.bathAppInForeground) {
                        a11y.launchBathApp()
                        relaunched = true
                        A11yService.awaitPageChangeAfter(mark, RuntimeConfig.pollIntervalMs)
                        continue
                    }
                    // 这三类页面不会自己变成 HOME：预约页要人点，UNKNOWN 多半是启动闪屏或
                    // 渲染到一半的树。宽限几轮让闪屏过去，仍认不出来就判失败并把设备收拾回首页
                    // ——空等到超时只会让浴室 App 一直停在陌生页面上给现场学生看。
                    //
                    // **不能回首页后继续等**：下一轮读到的 HOME 会被上面的分支当成正常终态，
                    // 于是一次失败被记成成功。主动导航出去的结果只有我们自己知道。
                    unknownRounds++
                    if (unknownRounds >= UNKNOWN_ROUNDS_BEFORE_FAIL) {
                        BathAppNavigator.navigateToHome(a11y, RuntimeConfig.homeBudgetMs)
                        return Terminal(false, "落在无法识别的页面")
                    }
                }
            }
            A11yService.awaitPageChangeAfter(mark, RuntimeConfig.pollIntervalMs)
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

    /**
     * 认出「无法识别的页面」前的宽限轮数。
     *
     * 浴室 App 启动时的闪屏、以及渲染到一半的树都会判成 `UNKNOWN`，立刻失败会误伤正常
     * 流程。5 轮 × 200ms 的轮询间隔 = 1 秒，够闪屏过去。
     */
    private const val UNKNOWN_ROUNDS_BEFORE_FAIL = 5
}
