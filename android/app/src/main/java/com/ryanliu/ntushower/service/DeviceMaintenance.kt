package com.ryanliu.ntushower.service

import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.log.LogTail
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.features.automation.BathAppRestart
import com.ryanliu.ntushower.features.automation.CommandOutcome
import com.ryanliu.ntushower.features.selfmanage.McuPowerSchedule
import com.ryanliu.ntushower.features.selfmanage.TimeSync
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import com.ryanliu.ntushower.transport.SupportedActions
import org.json.JSONObject
import java.io.File

/**
 * 设备自身的维护命令：执行 shell、读日志、重启本程序、重启整机、重启浴室 APP、
 * 设置系统时钟、重布定时开关机、读写免费名单。
 *
 * 独立于 [AgentService]：那是传输层的宿主，而这里的命令共同点是
 * **不经调度队列、即时执行**——重启本程序与重启整机会停掉本进程，排队没有意义；
 * 执行类是同步取回输出的短操作。
 *
 * 过期判定与调度器通道同一语义：服务端给的是相对预算（从收到算起），换算成本机
 * 单调钟上的截止点后，在**真正开始执行前**判——维护动作有真实副作用（clock.set
 * 拨钟、freelist.write 覆写文件、power.schedule 重布 MCU），服务端已经不再等结果的
 * 命令不执行。终结名动作（重启本程序/整机）同判：执行器是独立的、命令本身毫秒级，
 * 会过期只剩网络延迟一种来源，而那种延迟下"机器重启"仍是调用方要的结果与否，
 * 以统一语义为准——过期即拒，调用方重发。
 *
 * 重启本程序、重启整机**先回报再动作**：它们会杀死本进程，若不先回报，命令方只能等超时。
 */
// TooManyFunctions：维护动作按动作名一对一分派；合并会把每条动作各自的收尾保证藏进
// 一个长 when，反而更难核对。
@Suppress("TooManyFunctions")
object DeviceMaintenance {

    private const val TAG = "DeviceMaintenance"

    private const val DEFAULT_LOG_TAIL_LINES = 200
    private const val MAX_LOG_TAIL_LINES = 500

    /** 可远程读取的三份日志。路径固定，文件名来自 AppConfig / DeviceLog 的唯一定义。 */
    private val LOG_FILES = mapOf(
        "device" to DeviceLog.FILE_NAME,
        "crash" to AppConfig.CRASH_LOG_NAME,
        "ota" to AppConfig.OTA_INSTALL_LOG_NAME,
    )

    /** 本模块负责的 action。调用方据此判断是否要把它挪到工作线程。 */
    private val HANDLED = setOf(
        SupportedActions.APP_RESTART,
        SupportedActions.DEVICE_EXEC,
        SupportedActions.DEVICE_LOG_TAIL,
        SupportedActions.DEVICE_REBOOT,
        SupportedActions.BATH_RESTART,
        SupportedActions.CLOCK_SET,
        SupportedActions.POWER_SCHEDULE,
        SupportedActions.FREELIST_READ,
        SupportedActions.FREELIST_WRITE,
    )

    /**
     * 终结类动作：执行它们等于终止本进程。
     *
     * 这两个必须**立刻**执行，不与其他维护命令共用一个执行器。动作本身是毫秒级的——
     * [restartAgent] 与 [rebootMachine] 都只是起一个脱手进程（[RootShell.execDetached]，
     * 不等结果）；而共用执行器会让它们排在前面的命令之后，每条 `device.exec` 可以等满
     * 25 秒（见 [RootShell.command] 的超时上限）。真机上因此丢过一次恢复机会：一串超时
     * 的 `device.exec` 挡在前面，随后发的重启从服务端看也是超时，而设备侧一次都没执行。
     */
    private val TERMINATING = setOf(
        SupportedActions.APP_RESTART,
        SupportedActions.DEVICE_REBOOT,
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
     * `reported` 这个标记是必要的：`app.restart` 与 `device.reboot` 必须**先回报
     * 再执行**（那两个操作会杀掉本进程），所以它们的收尾动作跑在结果已发出之后。
     * 那之后崩了不影响服务端——它已经拿到答复了，不该再收到第二条。
     */
    fun handle(
        action: String,
        params: JSONObject,
        commandId: String,
        expireAtMs: Long,
        onOutcome: (CommandOutcome) -> Unit,
    ): Boolean {
        if (action !in HANDLED) return false
        if (expireAtMs > 0 && Uptime.nowMs() > expireAtMs) {
            Log.w(TAG, "maintenance command expired before start, rejecting: $commandId ($action)")
            onOutcome(
                CommandOutcome(
                    commandId,
                    OutcomeStatus.REJECTED,
                    ReasonCodes.COMMAND_DEADLINE_EXCEEDED,
                    "命令已过期，未执行",
                ),
            )
            return true
        }
        var reported = false
        try {
            when (action) {
                SupportedActions.APP_RESTART -> {
                    onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                    reported = true
                    restartAgent()
                }

                SupportedActions.DEVICE_EXEC -> {
                    onOutcome(execShell(params, commandId))
                    reported = true
                }

                SupportedActions.DEVICE_LOG_TAIL -> {
                    onOutcome(logTailOutcome(params, commandId))
                    reported = true
                }

                SupportedActions.DEVICE_REBOOT -> {
                    onOutcome(CommandOutcome(commandId, OutcomeStatus.OK, "", ""))
                    reported = true
                    rebootMachine()
                }

                SupportedActions.BATH_RESTART -> {
                    onOutcome(bathRestartOutcome(commandId))
                    reported = true
                }

                SupportedActions.CLOCK_SET -> {
                    onOutcome(clockSetOutcome(params, commandId))
                    reported = true
                }

                SupportedActions.POWER_SCHEDULE -> {
                    onOutcome(powerScheduleOutcome(params, commandId))
                    reported = true
                }

                SupportedActions.FREELIST_READ -> {
                    onOutcome(freelistReadOutcome(commandId))
                    reported = true
                }

                SupportedActions.FREELIST_WRITE -> {
                    onOutcome(freelistWriteOutcome(params, commandId))
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

    /**
     * 读取日志尾部：与 `device.exec` 分开，只读固定文件、固定行数与字节上限。
     *
     * `present=false` 是正常结果（crash.log 只在真正崩溃后才产生），不是失败；管理台
     * 据此能区分“设备上没有这份日志”与“上送链路没工作”。
     */
    private fun logTailOutcome(params: JSONObject, commandId: String): CommandOutcome {
        val kind = params.optString("kind", "device")
        val name = LOG_FILES[kind]
            ?: return CommandOutcome(
                commandId,
                OutcomeStatus.FAILED,
                ReasonCodes.DEVICE_INTERNAL_ERROR,
                "未知日志种类：$kind",
            )
        val lines = params.optInt("lines", DEFAULT_LOG_TAIL_LINES).coerceIn(1, MAX_LOG_TAIL_LINES)
        val result = LogTail.read(File(AppConfig.WORK_DIR, name), lines)
        return CommandOutcome(
            commandId,
            OutcomeStatus.OK,
            "",
            "",
            data = JSONObject()
                .put("content", result.content)
                .put("kind", kind)
                .put("lines", result.lines)
                .put("truncated", result.truncated)
                .put("present", result.present)
                .put("size_bytes", result.sizeBytes),
        )
    }

    /**
     * 重启整机。
     *
     * 同样脱手：整机一重启本进程就没了，不能挂在一个随它一起消失的常驻 shell 上。
     *
     * **"回来"这件事是有依据的**：本设备 ROM 不投递 `BOOT_COMPLETED`（`receiver.BootReceiver`
     * 那条路在这台机器上不成立），但开机后**系统会主动重绑已启用的无障碍服务**，于是
     * [A11yService.onServiceConnected] 被调到，它把 [AgentService] 拉起来——那是这台
     * 机器上唯一可靠的开机拉起锚点，旧系统真机验证过。标准 ROM 则另有 `BOOT_COMPLETED`。
     */
    private fun rebootMachine() {
        RootShell.execDetached("sleep 2; reboot")
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
        val ok = TimeSync.setWallClock(
            params.optString("datetime", ""),
            // rearm=false：hold 施加自己管理 MCU 布防——一次布防承担整晚
            // （ADR-0025），不需要写钟方配对重布
            params.optBoolean("rearm", true),
        )
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

    /** 重布定时开关机：off_at 断电、on_at 上电，都是设备表盘上的 HH:MM。 */
    private fun powerScheduleOutcome(params: JSONObject, commandId: String): CommandOutcome {
        val ok = McuPowerSchedule.schedule(params.optString("off_at", ""), params.optString("on_at", ""))
        return if (ok) {
            CommandOutcome(commandId, OutcomeStatus.OK, "", "")
        } else {
            CommandOutcome(
                commandId,
                OutcomeStatus.FAILED,
                ReasonCodes.DEVICE_INTERNAL_ERROR,
                "重布定时开关机失败（时刻格式或写入有误，见 device.log）",
            )
        }
    }

    /**
     * 读免费名单的原文。
     *
     * 返回原文而不在设备端拆行：行尾与分行是服务端的事（见 `freelist.read` 的注释），
     * 两端各拆一次必然分叉，而分叉的表现是名单里多出或少了几个学号。
     */
    private fun freelistReadOutcome(commandId: String): CommandOutcome = try {
        CommandOutcome(
            commandId,
            OutcomeStatus.OK,
            "",
            "",
            data = JSONObject().put("content", File(AppConfig.BATH_FREELIST_PATH).readText()),
        )
    } catch (e: Throwable) {
        Log.e(TAG, "读免费名单失败：${e.message}", e)
        CommandOutcome(
            commandId,
            OutcomeStatus.FAILED,
            ReasonCodes.DEVICE_FREELIST_READ_FAILED,
            "读取免费名单失败：${e.message}",
        )
    }

    /**
     * 写免费名单：整份替换。
     *
     * **原地截断重写，不落临时文件再改名**：那个目录（`readfile`）是浴室 App 读的，
     * 多出来的文件会被它一并读到。代价是它恰好在写入那一瞬读时会读到空文件——而空
     * 名单在浴室 App 那边的含义是"没人免费"，那是安全的方向。
     *
     * `writeText` 原样落字节、不做换行转换，所以行尾由服务端拼好再传进来。**空内容是
     * 合法内容**（名单为空即没人免费），不因它为空而拒绝；缺这个参数才是请求不成立。
     */
    private fun freelistWriteOutcome(params: JSONObject, commandId: String): CommandOutcome {
        if (!params.has("content")) {
            return CommandOutcome(
                commandId,
                OutcomeStatus.REJECTED,
                ReasonCodes.DEVICE_FREELIST_WRITE_FAILED,
                "命令里没有名单内容",
            )
        }
        return try {
            File(AppConfig.BATH_FREELIST_PATH).writeText(params.optString("content"))
            // 名单只在浴室 App 启动时被读一次，所以记一笔"下次拉起走完整通路"。
            // 不在这里接着拉起：那会把正在操作的学生切走——服务端的约定正是"名单在下次
            // 启动时生效，不急在那几十秒"（见 A11yRuntime.bathFullLaunchPending）。
            A11yRuntime.bathFullLaunchPending = true
            CommandOutcome(commandId, OutcomeStatus.OK, "", "")
        } catch (e: Throwable) {
            Log.e(TAG, "写免费名单失败：${e.message}", e)
            CommandOutcome(
                commandId,
                OutcomeStatus.FAILED,
                ReasonCodes.DEVICE_FREELIST_WRITE_FAILED,
                "写入免费名单失败：${e.message}",
            )
        }
    }

    /**
     * `device.exec` 的等待上限。
     *
     * 比服务端的 30 秒略短：让设备先超时并回报一次 timeout，而不是服务端等满上限
     * 后自己放弃——后者调用方只能得知未收到结果，无从判断命令是仍在执行还是已陷入停滞。
     */
    private const val EXEC_TIMEOUT_MS = 25_000L
}
