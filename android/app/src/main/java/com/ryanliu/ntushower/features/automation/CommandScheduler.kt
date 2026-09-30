package com.ryanliu.ntushower.features.automation

import android.util.Log
import com.ryanliu.ntushower.service.A11yService
import org.json.JSONObject
import java.util.concurrent.ConcurrentLinkedQueue
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
 * 命令调度器：分配/结算共享浴室 App UI 必须串行；拍照资源独立可并行。
 */
class CommandScheduler(private val overlay: CoverOverlay, private val onOutcome: (CommandOutcome) -> Unit) {

    sealed class Command(val id: String) {
        class Assign(id: String, val doorNo: Int, val certNo: String, val password: String) : Command(id)
        class Settle(id: String, val certNo: String, val password: String) : Command(id)
        class Photo(id: String) : Command(id)

        /** 全屏截图（远程调试验收能力，screenrecord+MediaCodec 方案）。 */
        class Screen(id: String) : Command(id)
    }

    private val queue = ConcurrentLinkedQueue<Command>()
    private val photoExecutor = Executors.newSingleThreadExecutor()
    private val automationExecutor = Executors.newSingleThreadExecutor()

    @Volatile private var executing = false

    fun enqueue(command: Command) {
        when (command) {
            is Command.Photo -> {
                Log.i(TAG, "photo enqueued: ${command.id}")
                photoExecutor.execute {
                    onOutcome(executeCapture(command))
                }
            }

            is Command.Screen -> {
                Log.i(TAG, "screen enqueued: ${command.id}")
                photoExecutor.execute {
                    onOutcome(executeCapture(command))
                }
            }

            else -> {
                Log.i(TAG, "enqueued: ${command.id} (${command::class.java.simpleName})")
                queue.add(command)
                drain()
            }
        }
    }

    @Synchronized
    private fun drain() {
        if (executing) return
        val command = queue.poll() ?: return
        executing = true
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
                onOutcome(CommandOutcome(command.id, "failed", "automation.internal_error", ""))
            } finally {
                executing = false
                drain()
            }
        }
    }

    private fun execute(command: Command, onNeedsFinish: (Boolean) -> Unit): CommandOutcome {
        val a11y = A11yService.instance
            ?: return CommandOutcome(command.id, "failed", "automation.no_a11y", "无障碍服务不可用")
        val action: AutomationFlow.Action = when (command) {
            is Command.Assign -> AssignAction(command.certNo, command.password, command.doorNo)
            is Command.Settle -> SettleAction(command.certNo, command.password)
            is Command.Photo, is Command.Screen -> return executeCapture(command)
        }
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

    private fun executeCapture(command: Command): CommandOutcome {
        // photo.capture=摄像头（Camera1）；screen.capture=全屏（screenrecord+MediaCodec）
        val imageBase64 = if (command is Command.Photo) {
            PhotoCapture.takePhoto()
        } else {
            com.ryanliu.ntushower.features.remote.ScreenCapture.captureJpegBase64()
        }
        val failedCode = if (command is Command.Photo) "photo.capture_failed" else "screen.capture_failed"
        return if (imageBase64 != null) {
            CommandOutcome(command.id, "ok", "", "", JSONObject().put("image_base64", imageBase64))
        } else {
            CommandOutcome(command.id, "failed", failedCode, "")
        }
    }

    companion object {
        private const val TAG = "CommandScheduler"
    }
}
