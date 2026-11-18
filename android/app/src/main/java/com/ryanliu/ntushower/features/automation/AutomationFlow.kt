package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.transport.ReasonCodes

/**
 * 流程失败：结构化错误码 + 人读描述。
 *
 * [reasonCode] 是跨端契约，**必须取 [ReasonCodes] 里的常量**：服务端按它分支、网页端按它选
 * 文案，写错一个字就会静默落到"提示不对"那一类，不报错也没人留意。
 *
 * [message] 是**这一次**失败的具体处境——哪个喷头、哪个按钮、卡在哪一步。服务端登记表里那句
 * 是这个码的通用含义，两句话是两件事：一个回答"怎么回事"，一个回答"这次卡在哪"。别把前者
 * 抄过来，抄过来就丢了上下文，排障的人只能看到一个笼统的结论。
 */
data class FlowError(val reasonCode: String, val message: String)

/**
 * 分配/结算共用流程模板（8 步，真机验证过的顺序）：
 *
 * 1 回首页 → 2 遮盖层 → 3 命令动作（可插拔）→ 4 等验证页
 * → 5 输入凭证 → 6 等浴室服务器响应（终点页捕获）→ 7 等回首页 → 8 撤遮盖 + 清选中态
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

        /**
         * 执行命令专属 UI 动作；返回 null 表示成功，返回 [FlowError] 表示失败。
         *
         * [trace] 由流程传入（开关关闭时是 null），动作可以往里插自己的分段标记。这一段在
         * 整条流程里最长，不细分就看不出它的耗时落在哪一步——真机上它在 101ms 与 807ms 之间
         * 跳，而每一步做了什么只有它自己知道。
         */
        fun perform(a11y: A11yNodeSource, trace: AutomationTrace.Trace?): FlowError?
    }

    fun run(a11y: A11yNodeSource, action: Action, overlay: CoverOverlay): FlowOutcome {
        // 开关关闭时 trace 为 null，各标记点退化成空操作——两条路径跑的是同一套代码
        val trace = AutomationTrace.begin(action.traceLabel)
        // 让状态尽快重算一次，但**不等它**。重算跑在自己的线程上，真机上量过，等一次要 165ms，
        // 而它换来的只是"起点处的帧更新一点"。真正决定要不要走导航的是 bathAppInForeground
        // （由窗口状态事件维护，本身即时）；帧旧顶多让导航多走一圈，结果不变。
        BathAppPage.refresh()
        trace?.mark("refresh-page")
        // 1 回首页
        if (!BathAppNavigator.navigateToHome(a11y, HOME_BUDGET_MS)) {
            trace?.end("home_unreachable")
            return FlowOutcome(error = FlowError(ReasonCodes.DEVICE_HOME_UNREACHABLE, "无法回到首页"))
        }
        trace?.mark("navigate-home")
        // 2 遮盖层（开关钉为常量，一直开）
        overlay.show()
        trace?.mark("overlay-show")
        try {
            // 3 命令动作
            action.perform(a11y, trace)?.let {
                overlay.hide()
                trace?.end("action_failed:${it.reasonCode}")
                return FlowOutcome(error = it)
            }
            trace?.mark("perform-action")

            // 4 等待验证页
            if (!BathAppNavigator.waitForPage(a11y, PageState.ID_VERIFY, CERT_VERIFY_WAIT_MS)) {
                overlay.hide()
                trace?.end("verify_page_timeout")
                return FlowOutcome(error = FlowError(ReasonCodes.DEVICE_VERIFY_PAGE_TIMEOUT, "无法进入验证页面"))
            }
            trace?.mark("wait-verify-page")

            // 5 输入账号密码（凭证由命令参数下发）
            if (!BathAppNavigator.inputCredentialsAndConfirm(a11y, action.certNo, action.password)) {
                overlay.hide()
                trace?.end("input_failed")
                return FlowOutcome(error = FlowError(ReasonCodes.DEVICE_LOGIN_INPUT_FAILED, "输入账号密码失败"))
            }
            trace?.mark("input-credentials")

            // 6 等待浴室服务器响应（终点页：HOME 成功 / HINT·VF_RESULT 拒绝）
            val terminal = awaitTerminal(a11y)
            if (!terminal.reached) {
                overlay.hide()
                trace?.end("bath_server_timeout")
                return FlowOutcome(error = FlowError(ReasonCodes.DEVICE_BATH_SERVER_TIMEOUT, terminal.message))
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

    /**
     * 流程收尾：等回首页 → 撤遮盖 → input tap 清除网格选中态。在结果回报之后调用。
     *
     * 分配和结算都会在喷头网格留下选中态，收尾都要清掉。`input tap` 走 root shell
     * 注入，`exec` 返回只代表事件已注入，不代表浴室 App 已经处理完；这里在 tap 后
     * 等一个 UI 静默窗口，把“上一条的收尾动作”和“下一条流程”隔开。
     */
    fun finish(a11y: A11yNodeSource, overlay: CoverOverlay) {
        try {
            BathAppNavigator.waitForPage(a11y, PageState.HOME, 10_000L)
        } finally {
            overlay.hide()
            val center = BathAppNavigator.getDateViewCenter(a11y)
            if (center != null) {
                RootShell.control.exec("input tap ${center.first} ${center.second}")
                awaitUiQuiet(uiSettleMs(), FINISH_SETTLE_TIMEOUT_MS)
            }
        }
    }

    /**
     * 收尾静默窗口：连续 [quietMs] 没有页面重算才算稳定，最多等 [timeoutMs]。
     * 页面一直在变时不要把下一条命令放出去。
     */
    private fun awaitUiQuiet(quietMs: Long, timeoutMs: Long) {
        var mark = A11yRuntime.pageChangeMark()
        val deadline = Uptime.nowMs() + timeoutMs
        while (true) {
            val left = deadline - Uptime.nowMs()
            if (left <= 0) return
            A11yRuntime.awaitPageChange(minOf(left, quietMs))
            val next = A11yRuntime.pageChangeMark()
            if (next == mark) return
            mark = next
        }
    }

    /** 点击后的静默窗口（[UI_SETTLE_MS]）。 */
    internal fun uiSettleMs(): Long = UI_SETTLE_MS

    private const val HOME_BUDGET_MS = 15_000L
    private const val CERT_VERIFY_WAIT_MS = 5_000L
    private const val BATH_RESPONSE_TIMEOUT_MS = 10_000L
    private const val UI_POLL_INTERVAL_MS = 60L
    private const val UI_SETTLE_MS = 200L
    private const val FINISH_SETTLE_TIMEOUT_MS = 2_000L

    private data class Terminal(val reached: Boolean, val message: String = "")

    /**
     * 等浴室服务器的终点：回首页即成功，提示页即被拒（附它给出的话）。
     *
     * ## 回首页不一定是成功
     *
     * 提交之后浴室 App 被切走、随后重启，落点同样是首页——它与"服务器处理完了"落在同一个
     * 页面上，只看页面分不开。分得开的是**中间经历过什么**：重启会走一遍启动闪屏，那期间
     * 浴室 App 已经在前台、却认不出页面；而正常完成是从验证页直接跳到首页，没有这一段。
     *
     * 所以判据是"离开过前台，且回来后在**前台**却认不出页面"。限定在前台是因为学生拉下
     * 通知栏时活动窗口变成 SystemUI，那时同样认不出页面——那是外部干扰，不是 App 在重建。
     *
     * 判成失败而不是继续等：两种情况给出的答复都是"不知道成没成"，而失败至少让调用方知道
     * 该去核对，成功则会让学生以为水已经开了。
     *
     * 拉回前台走 [BathAppNavigator.ensureForeground]（含关系统面板），不是裸的热切前台。
     */
    private fun awaitTerminal(a11y: A11yNodeSource): Terminal {
        val start = Uptime.nowMs()
        var leftForeground = false
        var rebuiltAfterLeave = false
        var unknownRounds = 0
        while (Uptime.nowMs() - start < BATH_RESPONSE_TIMEOUT_MS) {
            val mark = A11yRuntime.pageChangeMark()
            val inForeground = A11yRuntime.bathAppInForeground
            if (!inForeground) leftForeground = true
            val snapshot = BathAppPage.snapshot()
            if (leftForeground && inForeground && snapshot.state == PageState.UNKNOWN) {
                rebuiltAfterLeave = true
            }
            when (snapshot.state) {
                PageState.HOME ->
                    if (rebuiltAfterLeave) {
                        Terminal(false, "浴室 App 中途重启，无法确认这次操作的结果")
                    } else {
                        Terminal(true)
                    }

                PageState.ID_VERIFY -> Unit

                // 继续等待
                PageState.HINT, PageState.VF_RESULT -> return Terminal(true, snapshot.message)

                PageState.UNKNOWN, PageState.BOOKING, PageState.BOOKING_CODE -> {
                    // 不在浴室 App 里的时候认不出页面是必然的（活动窗口是别人的），把它拉回来
                    // 再说。恢复次数不设上限：这轮宽限计数只在浴室 App 位于前台时才累加，所以
                    // 反复被切走不会被误判成"落在无法识别的页面"。
                    if (!inForeground) {
                        BathAppNavigator.ensureForeground(a11y)
                        A11yRuntime.awaitPageChangeAfter(mark, UI_POLL_INTERVAL_MS)
                        continue
                    }
                    // 这三类页面不会自己变成 HOME：预约页要人点，UNKNOWN 通常是启动闪屏或
                    // 渲染到一半的树。宽限几轮让闪屏过去，仍认不出来就判失败并把设备收拾回首页
                    // ——空等到超时只会让浴室 App 一直停在陌生页面上给现场学生看。
                    //
                    // **不能回首页后继续等**：下一轮读到的 HOME 会被上面的分支当成正常终态，
                    // 于是一次失败被记成成功。主动导航出去的结果只有我们自己知道。
                    unknownRounds++
                    if (unknownRounds >= UNKNOWN_ROUNDS_BEFORE_FAIL) {
                        BathAppNavigator.navigateToHome(a11y, HOME_BUDGET_MS)
                        return Terminal(false, "落在无法识别的页面")
                    }
                }
            }
            A11yRuntime.awaitPageChangeAfter(mark, UI_POLL_INTERVAL_MS)
        }
        BathAppNavigator.navigateToHome(a11y, HOME_BUDGET_MS)
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
