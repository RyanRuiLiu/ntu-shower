package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.service.A11yService
import com.ryanliu.ntushower.service.UiChannel
import com.ryanliu.ntushower.service.UiKey
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

    fun enqueue(command: Command) {
        when (command) {
            // 图像采集资源独立，不占用自动化队列——否则一条慢截图会把
            // 浴室 APP 的操作序列堵住
            is Command.Photo, is Command.Screen -> {
                Log.i(TAG, "capture enqueued: ${command.id}")
                captureExecutor.execute { onOutcome(captureImage(command)) }
            }

            // 读节点树走自动化队列：它读的就是那份 UI 节点树，与 ui.touch / ui.key
            // 并行会读到操作到一半的界面。它和截图则毫无关系——早先归在采集队列是
            // 错的，那会让"看界面"和"读界面"互相排队，服务端并发取这两个时必有一个
            // 被拒（同通道互斥）
            else -> {
                Log.i(TAG, "enqueued: ${command.id} (${command::class.java.simpleName})")
                automationQueue.enqueue(command)?.let { runAutomation(it) }
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
                onOutcome(CommandOutcome(command.id, "failed", "device.internal_error", "设备端内部错误"))
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
            ?: return CommandOutcome(command.id, "failed", "device.a11y_unavailable", "无障碍服务不可用")
        A11yService.automationInProgress = true
        try {
            val result = AutomationFlow.run(a11y, action, overlay)
            if (result.error != null) {
                return CommandOutcome(command.id, "failed", result.error.reasonCode, result.error.message)
            }
            onNeedsFinish(true)
            return CommandOutcome(command.id, "ok", "", result.terminalMessage)
        } finally {
            A11yService.automationInProgress = false
        }
    }

    /**
     * 界面操作：触摸 / 滚动 / 按键 / 输入。
     *
     * 不要求无障碍可用：无障碍不在时这些操作退到 root shell（见 [UiChannel]），
     * 而那条通道整体失效时，界面操作正是远程唯一还能碰到设备的手段。
     *
     * 同样置 `automationInProgress`——这些操作会改变界面，若与崩溃守护的
     * 自动拉起并发，用户会看到界面被抢。
     */
    private fun executeUiAction(command: Command): CommandOutcome {
        val a11y = A11yService.instance
        A11yService.automationInProgress = true
        try {
            return when (command) {
                is Command.UiTouch -> {
                    val ok = if (command.toX != null && command.toY != null) {
                        UiChannel.swipe(command.x, command.y, command.toX, command.toY, command.durationMs)
                    } else {
                        UiChannel.tap(a11y, command.x, command.y)
                    }
                    outcome(command.id, ok, "ui.no_target_at_point", "该位置没有可点击的节点")
                }

                is Command.UiScroll -> {
                    val ok = UiChannel.scroll(a11y, command.forward)
                    outcome(command.id, ok, "ui.no_scrollable_container", "界面上没有可滚动的容器")
                }

                is Command.UiKey -> {
                    val key = UiKey.parse(command.key)
                        ?: return CommandOutcome(
                            command.id,
                            "failed",
                            "ui.unknown_key",
                            "不支持的按键：${command.key}",
                        )
                    outcome(command.id, UiChannel.key(a11y, key), "ui.key_rejected", "系统拒绝了该按键")
                }

                is Command.UiInput -> {
                    val ok = UiChannel.input(a11y, command.text)
                    outcome(command.id, ok, "ui.no_editable_field", "没有可输入的控件，或文本非 ASCII 而无障碍不可用")
                }

                is Command.UiDump -> exportTree(command)

                else -> CommandOutcome(command.id, "failed", "ui.unsupported_action", "不支持的操作")
            }
        } finally {
            A11yService.automationInProgress = false
        }
    }

    private fun outcome(id: String, ok: Boolean, failCode: String, failMessage: String): CommandOutcome =
        if (ok) CommandOutcome(id, "ok", "", "") else CommandOutcome(id, "failed", failCode, failMessage)

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
            CommandOutcome(command.id, "ok", "", "", JSONObject().put("image_base64", imageBase64))
        } else {
            CommandOutcome(command.id, "failed", failedCode, failedMessage)
        }
    }

    /**
     * 导出界面节点树，数据放在 `data.xml`——格式与 `uiautomator dump` 一致。
     *
     * 节点树没有第二条通道（理由见 [UiChannel] 的类注释），无障碍不在时如实失败。
     */
    private fun exportTree(command: Command): CommandOutcome {
        val tree = UiChannel.dump(A11yService.instance)
            ?: return CommandOutcome(command.id, "failed", "ui.no_window", "无障碍服务不可用或未能读取窗口")
        return CommandOutcome(command.id, "ok", "", "", JSONObject().put("xml", tree))
    }

    companion object {
        private const val TAG = "CommandScheduler"
    }
}
