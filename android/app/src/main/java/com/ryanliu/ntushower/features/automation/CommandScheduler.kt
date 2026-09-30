package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.service.A11yService
import com.ryanliu.ntushower.service.A11yUiChannel
import com.ryanliu.ntushower.service.ShellUiChannel
import com.ryanliu.ntushower.service.UiKey
import com.ryanliu.ntushower.service.UiTreeCodec
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONObject
import java.util.concurrent.Executor
import java.util.concurrent.Executors

/** 调度器产出的命令结果（与 session.command_result payload 对应）。 */
data class CommandOutcome(
    val commandId: String,
    val status: String, // ok | failed | blocked | timeout
    val reasonCode: String, // 结构化错误码；成功时为 ""
    val terminalMessage: String, // 浴室 App 结果页人读文本
    val data: JSONObject? = null, // photo.capture 携带 base64 图片
)

/**
 * 命令调度器：自动化类命令（分配/结算）经 [SerialCommandQueue] 串行放行——
 * 浴室 App 的 UI 操作必须串行，并发会让节点定位互相错位；
 * 拍照/截图资源独立，走单独执行器并行处理。
 */
class CommandScheduler(
    private val overlay: CoverOverlay,
    private val onOutcome: (CommandOutcome) -> Unit,
    private val automationExecutor: Executor = Executors.newSingleThreadExecutor(),
    private val captureExecutor: Executor = Executors.newSingleThreadExecutor(),
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
         * 滑动的落点取决于起点，滚动只表达"往哪边滚"。
         */
        class UiScroll(id: String, val forward: Boolean) : Command(id)

        /** 系统按键（返回/主页/最近任务/通知栏）。 */
        class UiKey(id: String, val key: String) : Command(id)

        /** 向焦点控件输入文本。 */
        class UiInput(id: String, val text: String) : Command(id)

        /** 导出界面节点树。 */
        class UiDump(id: String) : Command(id)
    }

    private val automationQueue = SerialCommandQueue<Command>()

    // 采集与自动化分开排队：图像采集不碰浴室 APP 的界面，一条慢截图不该把操作序列堵住。
    // 服务端那边它们也是两个通道，允许并发。
    private val captureQueue = SerialCommandQueue<Command>()

    fun enqueue(command: Command, expireAtMs: Long) {
        when (command) {
            // 图像采集资源独立，不占用自动化队列——否则一条慢截图会把
            // 浴室 APP 的操作序列堵住
            is Command.Photo, is Command.Screen -> dispatch(command, captureQueue, expireAtMs) { runCapture(it) }

            // 读节点树走自动化队列：它读的就是那份 UI 节点树，与 ui.touch / ui.key
            // 并行会读到操作到一半的界面。它和截图则毫无关系——早先归在采集队列是
            // 错的，那会让"看界面"和"读界面"互相排队，服务端并发取这两个时必有一个
            // 被拒（同通道互斥）
            else -> dispatch(command, automationQueue, expireAtMs) { runAutomation(it) }
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
        if (expireAtMs > 0 && System.nanoTime() / 1_000_000 > expireAtMs) {
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
            try {
                onOutcome(captureImage(command))
            } catch (e: Throwable) {
                Log.e(TAG, "capture crashed: ${e.message}", e)
                onOutcome(
                    CommandOutcome(
                        command.id,
                        OutcomeStatus.FAILED,
                        ReasonCodes.DEVICE_INTERNAL_ERROR,
                        "设备端内部错误",
                    ),
                )
            } finally {
                captureQueue.complete()?.let { runCapture(it) }
            }
        }
    }

    /** 执行一条自动化命令，结束后放行队列中的下一条。 */
    private fun runAutomation(command: Command) {
        automationExecutor.execute {
            var needsFinish = false
            try {
                val outcome = execute(command) { needsFinish = it }
                onOutcome(outcome) // 结果先回报
                if (needsFinish) { // 再做慢速收尾
                    A11yService.instance?.let { AutomationFlow.finish(it, overlay) }
                }
            } catch (e: Throwable) {
                Log.e(TAG, "command crashed: ${e.message}", e)
                onOutcome(
                    CommandOutcome(
                        command.id,
                        OutcomeStatus.FAILED,
                        ReasonCodes.DEVICE_INTERNAL_ERROR,
                        "设备端内部错误",
                    ),
                )
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
        val a11y = A11yService.instance
            ?: return CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.DEVICE_A11Y_UNAVAILABLE, "无障碍服务不可用")
        A11yService.automationInProgress = true
        try {
            val result = AutomationFlow.run(a11y, action, overlay)
            if (result.error != null) {
                return CommandOutcome(command.id, OutcomeStatus.FAILED, result.error.reasonCode, result.error.message)
            }
            onNeedsFinish(true)
            return CommandOutcome(command.id, OutcomeStatus.OK, "", result.terminalMessage)
        } finally {
            A11yService.automationInProgress = false
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
     * 两个例外都跟无障碍绑定：**输入**（必须支持中文）与**读节点树**（没有第二条
     * 通道，理由见 [UiTreeCodec]）。
     *
     * 同样置 `automationInProgress`——这些操作会改变界面，若与崩溃守护的自动拉起
     * 并发，用户会看到界面被抢。
     */
    private fun executeUiAction(command: Command): CommandOutcome {
        val a11y = A11yService.instance
        A11yService.automationInProgress = true
        try {
            return when (command) {
                is Command.UiTouch -> {
                    val ok = if (command.toX != null && command.toY != null) {
                        ShellUiChannel.swipe(command.x, command.y, command.toX, command.toY, command.durationMs)
                    } else {
                        ShellUiChannel.tap(command.x, command.y)
                    }
                    outcome(command.id, ok, "ui.no_target_at_point", "该位置没有可点击的节点")
                }

                is Command.UiScroll -> {
                    val ok = ShellUiChannel.scroll(command.forward)
                    outcome(command.id, ok, "ui.no_scrollable_container", "界面上没有可滚动的容器")
                }

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

                // 输入是唯一走无障碍的操作：它必须支持中文，而 shell 的 `input text`
                // 在本 ROM 上按字节处理，非 ASCII 会被写坏（见 A11yUiChannel 的类注释）
                is Command.UiInput -> {
                    val ok = a11y != null && A11yUiChannel.typeIntoFocus(a11y, command.text)
                    outcome(command.id, ok, ReasonCodes.UI_NO_EDITABLE_FIELD, "没有可输入的控件，或无障碍服务不可用")
                }

                is Command.UiDump -> exportTree(command)

                else -> CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.UI_UNSUPPORTED_ACTION, "不支持的操作")
            }
        } finally {
            A11yService.automationInProgress = false
        }
    }

    private fun outcome(id: String, ok: Boolean, failCode: String, failMessage: String): CommandOutcome = if (ok) {
        CommandOutcome(id, OutcomeStatus.OK, "", "")
    } else {
        CommandOutcome(id, OutcomeStatus.FAILED, failCode, failMessage)
    }

    private fun captureImage(command: Command): CommandOutcome {
        // photo=摄像头（Camera1）；screen=全屏（screenrecord+MediaCodec）
        val imageBase64 = if (command is Command.Photo) {
            PhotoCapture.takePhoto()
        } else {
            com.ryanliu.ntushower.features.remote.ScreenCapture.captureJpegBase64()
        }
        val (failedCode, failedMessage) = if (command is Command.Photo) {
            "camera.capture_failed" to "拍照失败"
        } else {
            "screen.capture_failed" to "截屏失败"
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
        val tree = A11yService.instance?.let { UiTreeCodec.export(it, RuntimeConfig.uiDumpMaxNodes.toInt()) }
            ?: return CommandOutcome(command.id, OutcomeStatus.FAILED, ReasonCodes.UI_NO_WINDOW, "无障碍服务不可用或未能读取窗口")
        return CommandOutcome(command.id, OutcomeStatus.OK, "", "", JSONObject().put("xml", tree))
    }

    companion object {
        private const val TAG = "CommandScheduler"
    }
}
