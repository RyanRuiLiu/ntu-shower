package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.features.remote.ScreenCapture
import com.ryanliu.ntushower.features.voice.VoicePlaySpec
import com.ryanliu.ntushower.features.voice.VoicePlayer
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONObject
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/** 命令执行队列的归属；与服务端 `ACTION_CHANNELS` 的通道划分一一对应。 */
enum class CommandChannel {
    AUTOMATION,
    CAPTURE,
    VOICE,
    PREFETCH,
}

/** 调度器产出的命令结果（与 session.command_result payload 对应）。 */
data class CommandOutcome(
    val commandId: String,
    // status 的合法取值唯一来源是 transport/OutcomeStatus（与服务端 outcomes.py 对账），
    // 这里不重复列举——列了就会漂移（此前写着 blocked，那个值早已废弃）
    val status: String,
    val reasonCode: String, // 结构化错误码；成功时为 ""
    val terminalMessage: String, // 浴室 App 结果页人读文本
    val data: JSONObject? = null, // photo/screen.capture 携带 base64 图片
)

/**
 * 命令调度器：自动化类命令（分配/结算）经 [SerialCommandQueue] 串行放行——
 * 浴室 App 的 UI 操作必须串行，并发会让节点定位互相错位；
 * 拍照/截图资源独立，走单独执行器并行处理。
 */
@Suppress("TooManyFunctions")
class CommandScheduler(
    private val overlay: CoverOverlay,
    private val voicePlayer: VoicePlayer,
    private val onOutcome: (CommandOutcome) -> Unit,
    private val automationExecutor: Executor = Executors.newSingleThreadExecutor(),
    private val captureExecutor: Executor = Executors.newSingleThreadExecutor(),
    private val voiceExecutor: Executor = Executors.newSingleThreadExecutor(),
    private val prefetchExecutor: Executor = Executors.newSingleThreadExecutor(),
) {

    sealed class Command(val id: String) {
        class Assign(id: String, val doorNo: Int, val certNo: String, val password: String) : Command(id)
        class Settle(id: String, val certNo: String, val password: String) : Command(id)
        class Photo(id: String) : Command(id)

        /** 全屏截图（远程调试验收能力，screenrecord+MediaCodec 方案）。 */
        class Screen(id: String) : Command(id)

        /**
         * 触摸：点击或滑动，按屏幕坐标。
         *
         * 元素定位应交给选择器（`resource-id` / `text` / `class`），而不是坐标或
         * 遍历序号——后者会随界面变化错位。选择器尚未实现，因此目前只按坐标。
         *
         * 给了 `toX`/`toY` 就是滑动，否则是点击。
         */
        class UiTouch(id: String, val x: Int, val y: Int, val toX: Int?, val toY: Int?, val durationMs: Int) :
            Command(id)

        /**
         * 滚动可滚动容器。
         *
         * 与 [UiTouch] 的滑动分开：这条作用于**容器**而不是坐标，语义不同——
         * 滑动的落点取决于起点，滚动只表达"往哪边滚"。[direction] 必须是
         * forward / backward 之一，由调用方明确给出，缺了或值不认识直接拒绝。
         */
        class UiScroll(id: String, val direction: String) : Command(id)

        /** 系统按键（返回/主页/最近任务/通知栏）。 */
        class UiKey(id: String, val key: String) : Command(id)

        /**
         * 向焦点控件输入文本。
         *
         * [channel] 是**并列的两条通路**，不是主备，调用方必须明确给值：
         * - `clipboard`——无障碍粘贴，支持中文；本 ROM 的粘贴在"同一字段第二次插入"
         *   时会带出一个空格（ROM widget 行为）；
         * - `keys`——shell `input text`（仅 ASCII），无空格问题。
         * 网页端给使用者一个开关自己选。值缺失或不认识时命令被拒绝，不做静默回落。
         */
        class UiInput(id: String, val text: String, val channel: String) : Command(id)

        /** 导出界面节点树。 */
        class UiDump(id: String) : Command(id)

        /** 按播放清单顺序播放固定音频与停顿。参数在执行时解析。 */
        class PlayVoice(id: String, val params: JSONObject) : Command(id)

        /** 把音频排入低优先级后台预下载队列，不播放、不等待网络。 */
        class PrepareVoice(id: String, val params: JSONObject) : Command(id)
    }

    private val automationQueue = SerialCommandQueue<Command>()

    // 采集与自动化分开排队：图像采集不碰浴室 APP 的界面，一条慢截图不该把操作序列堵住。
    // 服务端那边它们也是两个通道，允许并发。
    private val captureQueue = SerialCommandQueue<Command>()

    // 语音独立排队：下载/播放不碰界面，也不该被分配/结算或截图堵住；同设备多条语音串行。
    private val voiceQueue = SerialCommandQueue<Command>()

    // 预下载只把素材排入后台队列、立即返回；单独排队，播放永远不用等它。
    private val prefetchQueue = SerialCommandQueue<Command>()

    fun enqueue(command: Command, expireAtMs: Long) {
        when (channelOf(command)) {
            // 图像采集资源独立，不占用自动化队列——否则一条慢截图会把
            // 浴室 APP 的操作序列堵住
            CommandChannel.CAPTURE -> dispatch(command, captureQueue, expireAtMs) { runCapture(it) }

            // 语音独立执行器：与自动化、采集互不阻塞
            CommandChannel.VOICE -> dispatch(command, voiceQueue, expireAtMs) {
                runVoice(it)
            }

            CommandChannel.PREFETCH -> dispatch(command, prefetchQueue, expireAtMs) {
                runPrefetch(it)
            }

            // 读节点树走自动化队列：它读的就是那份 UI 节点树，与 ui.touch / ui.key
            // 并行会读到操作到一半的界面。它和截图则毫无关系——早先归在采集队列是
            // 错的，那会让"看界面"和"读界面"互相排队，服务端并发取这两个时必有一个
            // 被拒（同通道互斥）
            CommandChannel.AUTOMATION -> dispatch(command, automationQueue, expireAtMs) { runAutomation(it) }
        }
    }

    /**
     * 按队列的裁决决定"立刻跑、排队、还是拒绝"。
     *
     * 两件事在这里合流：
     *
     * **过期**——[expireAtMs] 是本机单调时钟上的一个点，超过它就没必要再做了。服务端
     * 那时已经不再等结果，而命令是有真实副作用的（分配会写浴室 App 的库、界面注入会
     * 动到别人正在用的会话）。判在这里而不是收到命令的那一刻：排队等待正是要管的场景。
     *
     * **排满**——如实回报"过载"。这类情况主要出现在服务端多次超时之后：它每次超时都
     * 释放锁放行下一条，而设备这边可能还在跑上一条，排队数于是被超时次数推高。拒掉比
     * 继续堆着好，堆着的那些迟早也会超时。
     */
    private fun dispatch(
        command: Command,
        queue: SerialCommandQueue<Command>,
        expireAtMs: Long,
        run: (Command) -> Unit,
    ) {
        if (expireAtMs > 0 && Uptime.nowMs() > expireAtMs) {
            Log.w(TAG, "command expired before start, rejecting: ${command.id}")
            onOutcome(
                CommandOutcome(
                    command.id,
                    OutcomeStatus.REJECTED,
                    ReasonCodes.COMMAND_DEADLINE_EXCEEDED,
                    "命令已过期，未执行",
                ),
            )
            return
        }
        when (val result = queue.enqueue(command)) {
            is SerialCommandQueue.EnqueueResult.Run -> {
                Log.i(TAG, "enqueued: ${command.id} (${command::class.java.simpleName})")
                run(result.item)
            }

            SerialCommandQueue.EnqueueResult.Queued -> Log.i(TAG, "queued: ${command.id}")

            SerialCommandQueue.EnqueueResult.Rejected -> {
                Log.w(TAG, "queue full, rejecting: ${command.id}")
                onOutcome(
                    CommandOutcome(
                        command.id,
                        OutcomeStatus.REJECTED,
                        ReasonCodes.SERVER_OVERLOADED,
                        "设备端待处理命令过多，请稍后再试",
                    ),
                )
            }
        }
    }

    /** 执行一条采集命令，结束后放行采集队列中的下一条。 */
    private fun runCapture(command: Command) {
        captureExecutor.execute {
            var reported = false
            try {
                onOutcome(captureImage(command))
                reported = true
            } catch (e: Throwable) {
                if (reported) {
                    Log.w(TAG, "结果已回报，收尾步骤失败：${e.message}", e)
                } else {
                    Log.e(TAG, "capture crashed: ${e.message}", e)
                    onOutcome(
                        CommandOutcome(
                            command.id,
                            OutcomeStatus.FAILED,
                            ReasonCodes.DEVICE_INTERNAL_ERROR,
                            "设备端内部错误",
                        ),
                    )
                }
            } finally {
                captureQueue.complete()?.let { runCapture(it) }
            }
        }
    }

    /** 执行一条语音命令，结束后放行语音队列中的下一条。 */
    private fun runVoice(command: Command) {
        voiceExecutor.execute {
            var reported = false
            try {
                onOutcome(executeVoice(command))
                reported = true
            } catch (e: Throwable) {
                if (reported) {
                    Log.w(TAG, "结果已回报，语音收尾失败：${e.message}", e)
                } else {
                    Log.e(TAG, "voice crashed: ${e.message}", e)
                    onOutcome(
                        CommandOutcome(
                            command.id,
                            OutcomeStatus.FAILED,
                            ReasonCodes.DEVICE_INTERNAL_ERROR,
                            "设备端内部错误",
                        ),
                    )
                }
            } finally {
                voiceQueue.complete()?.let { runVoice(it) }
            }
        }
    }

    /** 执行一条预下载命令：只入后台队列，不等待网络。 */
    private fun runPrefetch(command: Command) {
        prefetchExecutor.execute {
            var reported = false
            try {
                onOutcome(executeVoice(command))
                reported = true
            } catch (e: Throwable) {
                if (!reported) {
                    Log.e(TAG, "prefetch crashed: ${e.message}", e)
                    onOutcome(
                        CommandOutcome(
                            command.id,
                            OutcomeStatus.FAILED,
                            ReasonCodes.DEVICE_INTERNAL_ERROR,
                            "设备端内部错误",
                        ),
                    )
                }
            } finally {
                prefetchQueue.complete()?.let { runPrefetch(it) }
            }
        }
    }

    private fun executeVoice(command: Command): CommandOutcome {
        val result = when (command) {
            is Command.PlayVoice -> {
                val spec = VoicePlaySpec.parse(command.params) ?: return invalidVoice(command)
                voicePlayer.play(spec)
            }

            is Command.PrepareVoice -> {
                val clips = VoicePlaySpec.parsePrepare(command.params) ?: return invalidVoice(command)
                voicePlayer.prepare(clips)
            }

            else -> return invalidVoice(command)
        }
        return CommandOutcome(command.id, result.status, result.reasonCode, result.message, result.data)
    }

    private fun invalidVoice(command: Command): CommandOutcome = CommandOutcome(
        command.id,
        OutcomeStatus.FAILED,
        ReasonCodes.VOICE_PARAMS_INVALID,
        "播报参数不合法",
    )

    /**
     * 执行一条自动化命令，结束后放行队列中的下一条。
     *
     * **结果只回报一次**（`reported` 标记，与 [com.ryanliu.ntushower.service.DeviceMaintenance]
     * 同构）：回报之后还有慢速收尾（等回首页、撤遮盖、清选中态），那一步失败不能补发第二条
     * ——服务端按 command_id 关联，收到第二条只会让"这条命令到底成没成"多出一个互相矛盾的
     * 答案，而第一条才是真的。
     */
    private fun runAutomation(command: Command) {
        automationExecutor.execute {
            var needsFinish = false
            var reported = false
            try {
                val outcome = execute(command) { needsFinish = it }
                onOutcome(outcome) // 结果先回报
                reported = true
                if (needsFinish) { // 再做慢速收尾
                    // 只有分配会选中网格项；结算不要 tap，避免收尾动作撞上下一条补分配
                    A11yRuntime.source?.let {
                        AutomationFlow.finish(it, overlay, clearGrid = command is Command.Assign)
                    }
                }
            } catch (e: Throwable) {
                if (reported) {
                    Log.w(TAG, "结果已回报，收尾步骤失败：${e.message}", e)
                } else {
                    Log.e(TAG, "command crashed: ${e.message}", e)
                    onOutcome(
                        CommandOutcome(
                            command.id,
                            OutcomeStatus.FAILED,
                            ReasonCodes.DEVICE_INTERNAL_ERROR,
                            "设备端内部错误",
                        ),
                    )
                }
            } finally {
                automationQueue.complete()?.let { runAutomation(it) }
            }
        }
    }

    private fun execute(command: Command, onNeedsFinish: (Boolean) -> Unit): CommandOutcome {
        val action: AutomationFlow.Action = when (command) {
            is Command.Assign -> AssignAction(command.certNo, command.password, command.doorNo)

            is Command.Settle -> SettleAction(command.certNo, command.password)

            // 界面操作不经过浴室 APP 的固定流程，直接作用于当前界面
            else -> return executeUiAction(command)
        }
        // 会话流程完全由无障碍驱动浴室 APP 的界面，没有第二条通道
        val a11y = A11yRuntime.source
            ?: return CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.DEVICE_A11Y_UNAVAILABLE, "无障碍服务不可用")
        A11yRuntime.automationInProgress = true
        try {
            val result = AutomationFlow.run(a11y, action, overlay)
            if (result.error != null) {
                return CommandOutcome(command.id, OutcomeStatus.FAILED, result.error.reasonCode, result.error.message)
            }
            onNeedsFinish(true)
            return CommandOutcome(command.id, OutcomeStatus.OK, "", result.terminalMessage)
        } finally {
            A11yRuntime.automationInProgress = false
        }
    }

    /**
     * 界面检视的手动操作：触摸 / 滑动 / 滚动 / 按键 / 输入 / 读树。
     *
     * **大部分不要求无障碍可用**——点击、滑动、滚动、按键都走 root shell 的
     * `input` 命令（见 [ShellUiChannel]）。无障碍整体失效时（总开关被系统置 0
     * 且拒绝写回，恢复办法见 `A11yGuard`），这几项照常工作，是那种局面下远程
     * 唯一还能碰到设备界面的手段。
     *
     * 两个例外跟无障碍有关：**读节点树**没有第二条通道（理由见 [UiTreeCodec]）；
     * **输入**的粘贴通道用无障碍（[A11yUiChannel]），另一条 `keys` 通道走 shell——
     * 无障碍整体失效时（恢复办法见 `A11yGuard`），输入切到按键通道、加上本组其余
     * shell 操作，仍然是远程能碰到设备界面的手段。
     *
     * 同样置 `automationInProgress`——这些操作会改变界面，若与崩溃守护的自动拉起
     * 并发，用户会看到界面被抢。
     */
    private fun executeUiAction(command: Command): CommandOutcome {
        val a11y = A11yRuntime.source
        A11yRuntime.automationInProgress = true
        try {
            return when (command) {
                is Command.UiTouch -> {
                    val ok = if (command.toX != null && command.toY != null) {
                        ShellUiChannel.swipe(command.x, command.y, command.toX, command.toY, command.durationMs)
                    } else {
                        ShellUiChannel.tap(command.x, command.y)
                    }
                    outcome(command.id, ok, ReasonCodes.UI_NO_TARGET_AT_POINT, "该位置没有可点击的节点")
                }

                is Command.UiScroll -> executeScroll(command)

                is Command.UiKey -> {
                    val key = UiKey.parse(command.key)
                        ?: return CommandOutcome(
                            command.id,
                            OutcomeStatus.FAILED,
                            ReasonCodes.UI_UNKNOWN_KEY,
                            "不支持的按键：${command.key}",
                        )
                    outcome(command.id, ShellUiChannel.key(key), ReasonCodes.UI_KEY_REJECTED, "系统拒绝了该按键")
                }

                is Command.UiInput -> executeInput(command, a11y)

                is Command.UiDump -> exportTree(command)

                else -> CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.UI_UNSUPPORTED_ACTION, "不支持的操作")
            }
        } finally {
            A11yRuntime.automationInProgress = false
        }
    }

    private fun outcome(id: String, ok: Boolean, failCode: String, failMessage: String): CommandOutcome = if (ok) {
        CommandOutcome(id, OutcomeStatus.OK, "", "")
    } else {
        CommandOutcome(id, OutcomeStatus.FAILED, failCode, failMessage)
    }

    /**
     * 滚动：方向是必填的闭集，缺失或不认识直接拒绝，不做静默回落。
     */
    private fun executeScroll(command: Command.UiScroll): CommandOutcome = when (command.direction) {
        CommandFactory.DIRECTION_FORWARD -> {
            val ok = ShellUiChannel.scroll(forward = true)
            outcome(command.id, ok, ReasonCodes.UI_NO_SCROLLABLE_CONTAINER, "界面上没有可滚动的容器")
        }

        CommandFactory.DIRECTION_BACKWARD -> {
            val ok = ShellUiChannel.scroll(forward = false)
            outcome(command.id, ok, ReasonCodes.UI_NO_SCROLLABLE_CONTAINER, "界面上没有可滚动的容器")
        }

        else -> CommandOutcome(
            command.id,
            OutcomeStatus.FAILED,
            ReasonCodes.UI_UNKNOWN_DIRECTION,
            "未知的滚动方向：${command.direction}",
        )
    }

    /**
     * 输入有两条并列通道：剪贴板粘贴支持中文，但本 ROM 的粘贴在第二次插入时会带出
     * 空格；按键注入没有空格问题却只走得通 ASCII——所以按键通道在这里再守一次
     * ASCII，守不住就明确拒绝，不把写坏的内容静默落进输入框。选哪条由网页端的
     * 使用者定；通道值缺失或不认识直接拒绝，不做静默回落。
     */
    private fun executeInput(command: Command.UiInput, a11y: A11yNodeSource?): CommandOutcome {
        when (command.channel) {
            "keys" -> {
                if (!command.text.all { it.code in 32..126 }) {
                    return CommandOutcome(
                        command.id,
                        OutcomeStatus.FAILED,
                        ReasonCodes.UI_KEYS_ASCII_ONLY,
                        "按键通道仅支持英文、数字与符号，中文请用粘贴通道",
                    )
                }
                val ok = ShellUiChannel.typeText(command.text)
                return outcome(command.id, ok, ReasonCodes.UI_TEXT_REJECTED, "系统拒绝了文本注入")
            }

            "clipboard" -> {
                val ok = a11y != null && A11yUiChannel.typeIntoFocus(a11y, command.text)
                return outcome(command.id, ok, ReasonCodes.UI_NO_EDITABLE_FIELD, "没有可输入的控件，或无障碍服务不可用")
            }

            else -> return CommandOutcome(
                command.id,
                OutcomeStatus.FAILED,
                ReasonCodes.UI_UNKNOWN_CHANNEL,
                "未知的输入通道：${command.channel}",
            )
        }
    }

    private fun captureImage(command: Command): CommandOutcome {
        // photo=摄像头（Camera1）；screen=全屏（screenrecord+MediaCodec）
        val imageBase64 = if (command is Command.Photo) {
            PhotoCapture.takePhoto()
        } else {
            ScreenCapture.captureJpegBase64()
        }
        val (failedCode, failedMessage) = if (command is Command.Photo) {
            ReasonCodes.CAMERA_CAPTURE_FAILED to "拍照失败"
        } else {
            ReasonCodes.SCREEN_CAPTURE_FAILED to "截屏失败"
        }
        return if (imageBase64 != null) {
            CommandOutcome(command.id, OutcomeStatus.OK, "", "", JSONObject().put("image_base64", imageBase64))
        } else {
            CommandOutcome(command.id, OutcomeStatus.FAILED, failedCode, failedMessage)
        }
    }

    /**
     * 导出界面节点树，数据放在 `data.xml`——格式与 `uiautomator dump` 一致。
     *
     * 节点树没有第二条通道：shell 侧的 `uiautomator dump` 会经 `UiAutomation` 接管
     * 系统无障碍会话，真机上出现过用它取几次节点后整机无障碍失效（因果未坐实，
     * 但风险远大于便利）。无障碍不在时如实失败，不给一个更差的替代。
     */
    private fun exportTree(command: Command): CommandOutcome {
        val tree = A11yRuntime.source?.let { UiTreeCodec.export(it, RuntimeConfig.uiDumpMaxNodes.toInt()) }
            ?: return CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.UI_NO_WINDOW, "无障碍服务不可用或未能读取窗口")
        return CommandOutcome(command.id, OutcomeStatus.OK, "", "", JSONObject().put("xml", tree))
    }

    companion object {
        private const val TAG = "CommandScheduler"

        /**
         * 命令落哪条队列。这是服务端 `ACTION_CHANNELS` 通道划分在设备端的镜像，
         * 由 [CommandSchedulerChannelTest] 与服务端逐动作对账——改任一侧都要两同步。
         */
        fun channelOf(command: Command): CommandChannel = when (command) {
            is Command.Photo, is Command.Screen -> CommandChannel.CAPTURE
            is Command.PlayVoice -> CommandChannel.VOICE
            is Command.PrepareVoice -> CommandChannel.PREFETCH
            else -> CommandChannel.AUTOMATION
        }
    }
}
