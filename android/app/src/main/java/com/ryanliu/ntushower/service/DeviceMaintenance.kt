package com.ryanliu.ntushower.service

import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.BathAppRestart
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.selfmanage.TimeSync
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject

/**
 * 设备自身的维护命令：执行 shell、重启 agent、卸载。
 *
 * 独立于 [AgentService]：那是传输层的宿主，而这三个命令的共同点是
 * **不经调度队列、即时执行**——重启与卸载会停掉本进程，排队没有意义；
 * 执行类是同步取回输出的短操作。
 *
 * 重启与卸载**先回报再动作**：它们会杀死本进程，若不先回报，命令方只能等超时。
 */
object DeviceMaintenance {

    private const val TAG = "DeviceMaintenance"

    /** 本模块负责的 action。调用方据此判断是否要把它挪到工作线程。 */
    private val HANDLED = setOf(
        SupportedActions.DEVICE_RESTART,
        SupportedActions.DEVICE_EXEC,
        SupportedActions.DEVICE_UNINSTALL,
        SupportedActions.BATH_RESTART,
        SupportedActions.CLOCK_SET,
    )

    /**
     * 终结类动作：执行它们等于终止本进程。
     *
     * 这两个必须**立刻**执行，不与其他维护命令共用一个执行器。动作本身是毫秒级的——
     * [restartAgent] 与 [uninstallAgent] 都只是起一个脱手进程（[RootShell.execDetached]，
     * 不等结果）；而共用执行器会让它们排在前面的命令之后，每条 `device.exec` 可以等满
     * 25 秒（见 [RootShell.command] 的超时上限）。真机上因此丢过一次恢复机会：一串超时
     * 的 `device.exec` 挡在前面，随后发的重启从服务端看也是超时，而设备侧一次都没执行。
     */
    private val TERMINATING = setOf(
        SupportedActions.DEVICE_RESTART,
        SupportedActions.DEVICE_UNINSTALL,
    )

    /**
     * 本模块是否处理该 action。
     *
     * 存在这个判断是因为 [handle] **不能在主线程上调用**（理由见 [AgentService] 的
     * 分发处），调用方要先分类、再决定在哪个线程上执行。
     */
    fun handles(action: String): Boolean = action in HANDLED

    /**
     * 该动作是否属于终结类。调用方据此选执行器：终结类走独立的即时执行器，其余走共用队列。
     */
    fun isTerminating(action: String): Boolean = action in TERMINATING

    /**
     * 处理一条维护命令；不属于此类返回 false。
     *
     * 结果经 [onOutcome] 交回调用方发送——这里不持有连接，也不留可变状态。
     *
     * **保证回报一次**：服务端在等一个有终点的答复，任何漏网的异常都会让那条命令永远
     * 悬着——服务端只能等到超时，而设备这边连"我失败了"都说不出来。[CommandScheduler]
     * 对自动化命令有同样的保证。
     *
     * `reported` 这个标记是必要的：`device.restart` 与 `device.uninstall` 必须**先回报
     * 再执行**（那两个操作会杀掉本进程），所以它们的收尾动作跑在结果已发出之后。
     * 那之后崩了不影响服务端——它已经拿到答复了，不该再收到第二条。
     */
    fun handle(action: String, params: JSONObject, commandId: String, onOutcome: (CommandOutcome) -> Unit): Boolean {
        if (action !in HANDLED) return false
        var reported = false
        try {
            when (action) {
                SupportedActions.DEVICE_RESTART -> {
                    onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                    reported = true
                    restartAgent()
                }

                SupportedActions.DEVICE_EXEC -> {
                    onOutcome(execShell(params, commandId))
                    reported = true
                }

                SupportedActions.DEVICE_UNINSTALL -> {
                    onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                    reported = true
                    uninstallAgent()
                }

                SupportedActions.BATH_RESTART -> {
                    onOutcome(bathRestartOutcome(commandId))
                    reported = true
                }

                SupportedActions.CLOCK_SET -> {
                    onOutcome(clockSetOutcome(params, commandId))
                    reported = true
                }
            }
        } catch (e: Throwable) {
            if (reported) {
                Log.w(TAG, "结果已回报，收尾步骤失败：${e.message}", e)
            } else {
                Log.e(TAG, "维护命令崩溃：$action (${e.message})", e)
                onOutcome(
                    CommandOutcome(
                        commandId,
                        OutcomeStatus.FAILED,
                        ReasonCodes.DEVICE_INTERNAL_ERROR,
                        "设备端内部错误",
                    ),
                )
            }
        }
        return true
    }

    /**
     * 重启 agent 进程。
     *
     * 脱手执行：本进程随后会被强制停止，这条命令必须挂在一个不随它一起消失的
     * 进程上——常驻 shell 是本进程的子进程，会一起死。
     *
     * 这里保留 `am force-stop`：它会把应用打进 stopped 状态，导致 AlarmManager 与
     * 广播接收者失效，因此**后面必须紧跟 `am startservice`** 把它拉出 stopped——
     * 少了这一步，重启后的进程不会有看门狗闹钟，等于自断恢复链路。
     */
    private fun restartAgent() {
        val appId = BuildConfig.APPLICATION_ID
        RootShell.execDetached(
            "sleep 2; am force-stop $appId; sleep 2; am startservice -n $appId/.service.AgentService",
        )
    }

    /**
     * 执行一条 root shell 命令，输出放在 `data.output`。
     *
     * 走 `command` 独占通道：命令内容由调用方给、**不可预期**，与界面操作共用控制通道
     * 会让一条慢命令把界面注入一起堵住。
     */
    private fun execShell(params: JSONObject, commandId: String): CommandOutcome {
        val result = RootShell.command.exec(params.optString("command", ""), EXEC_TIMEOUT_MS)

        // 三种去向分开，别都归到 failed：
        // - timedOut：超过时限，**不知道成没成**，客户端据此可以决定是否重试
        // - exitCode 非 0：跑完了、失败了，重试大概还是失败
        // - exitCode 为 0：跑完了、成了
        val status = when {
            result.timedOut -> OutcomeStatus.TIMEOUT
            result.exitCode == 0 -> OutcomeStatus.OK
            else -> OutcomeStatus.FAILED
        }
        val reasonCode = when {
            result.timedOut -> ReasonCodes.COMMAND_DEADLINE_EXCEEDED
            result.exitCode == 0 -> ""
            else -> ReasonCodes.DEVICE_EXEC_FAILED
        }
        val message = if (status == OutcomeStatus.OK) "" else result.output

        return CommandOutcome(
            commandId,
            status,
            reasonCode,
            terminalMessage = message,
            data = JSONObject()
                .put("output", result.output)
                .put("exit_code", result.exitCode),
        )
    }

    /** 卸载本 agent。同样脱手：卸载会杀掉本进程。 */
    private fun uninstallAgent() {
        RootShell.execDetached("sleep 2; pm uninstall ${BuildConfig.APPLICATION_ID}")
    }

    /** 重启浴室 App：强停与冷启动都不杀本进程，可以同步等结果再回报。 */
    private fun bathRestartOutcome(commandId: String): CommandOutcome {
        val ok = BathAppRestart.restart()
        return if (ok) {
            CommandOutcome(commandId, OutcomeStatus.OK, "", "")
        } else {
            CommandOutcome(
                commandId,
                OutcomeStatus.FAILED,
                ReasonCodes.DEVICE_INTERNAL_ERROR,
                "重启浴室 App 的 shell 提交失败",
            )
        }
    }

    /** 设置设备系统时钟：datetime 由网页端给定（设备本地墙钟时间）。 */
    private fun clockSetOutcome(params: JSONObject, commandId: String): CommandOutcome {
        val ok = TimeSync.setWallClock(params.optString("datetime", ""))
        return if (ok) {
            CommandOutcome(commandId, OutcomeStatus.OK, "", "")
        } else {
            CommandOutcome(
                commandId,
                OutcomeStatus.FAILED,
                ReasonCodes.DEVICE_INTERNAL_ERROR,
                "设置系统时钟失败（格式或写入有误，见 device.log）",
            )
        }
    }

    /**
     * `device.exec` 的等待上限。
     *
     * 比服务端的 30 秒略短：让设备先超时并回报一次 timeout，而不是服务端等满上限
     * 后自己放弃——后者调用方只知道"没等到"，无从判断命令是还在跑还是已经卡死。
     */
    private const val EXEC_TIMEOUT_MS = 25_000L
}
