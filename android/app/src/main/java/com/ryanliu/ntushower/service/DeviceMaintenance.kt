package com.ryanliu.ntushower.service

import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.CommandOutcome
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

    /** 本模块负责的 action。调用方据此判断是否要把它挪到工作线程。 */
    private val HANDLED = setOf(
        SupportedActions.DEVICE_RESTART,
        SupportedActions.DEVICE_EXEC,
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
     * 处理一条维护命令；不属于此类返回 false。
     *
     * 结果经 [onOutcome] 交回调用方发送——这里不持有连接，也不留可变状态。
     */
    fun handle(action: String, params: JSONObject, commandId: String, onOutcome: (CommandOutcome) -> Unit): Boolean {
        when (action) {
            SupportedActions.DEVICE_RESTART -> {
                onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                restartAgent()
            }

            SupportedActions.DEVICE_EXEC -> onOutcome(execShell(params, commandId))

            SupportedActions.DEVICE_UNINSTALL -> {
                onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                uninstallAgent()
            }

            // 正常路径下调用方已用 handles 判过；这里只是防御直接调用
            else -> return false
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
     * 用一次性独立进程：命令内容由调用方给、**不可预期**，走常驻通道会占住它的
     * 串行锁直到超时，把门状态上报与界面注入一起堵住。
     */
    private fun execShell(params: JSONObject, commandId: String): CommandOutcome {
        // 走 command 通道（独占一条常驻 shell）：命令内容由调用方给、不可预期，
        // 与界面操作共用控制通道会让一条慢命令把界面注入一起堵住
        val result = RootShell.command.exec(params.optString("command", ""), EXEC_TIMEOUT_MS)

        // 三种去向分开，别都归到 failed：
        // - timedOut：超过时限，**不知道成没成**，客户端据此可以决定是否重试
        // - exitCode 非 0：跑完了、失败了，重试大概还是失败
        // - exitCode 读不到：命令确实跑完了（输出被截断时会这样），按成功算
        val status = when {
            result.timedOut -> OutcomeStatus.TIMEOUT
            result.success -> OutcomeStatus.OK
            else -> OutcomeStatus.FAILED
        }
        val reasonCode = when {
            result.timedOut -> ReasonCodes.COMMAND_DEADLINE_EXCEEDED
            result.success -> ""
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
                .put("exit_code", result.exitCode ?: JSONObject.NULL),
        )
    }

    /** 卸载本 agent。同样脱手：卸载会杀掉本进程。 */
    private fun uninstallAgent() {
        RootShell.execDetached("sleep 2; pm uninstall ${BuildConfig.APPLICATION_ID}")
    }

    /**
     * `device.exec` 的等待上限。
     *
     * 比服务端的 30 秒略短：让设备先超时并回报一次 timeout，而不是服务端等满上限
     * 后自己放弃——后者调用方只知道"没等到"，无从判断命令是还在跑还是已经卡死。
     */
    private const val EXEC_TIMEOUT_MS = 25_000L
}
