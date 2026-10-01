package com.ryanliu.ntushower.core.audit

import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.logfile.CappedLogFile
import org.json.JSONObject
import java.io.File

/**
 * 设备端命令痕迹：一条一行 JSON（JSONL），与 stdout Logcat 互为备份。
 *
 * ## 为什么文件而不是只靠 Logcat
 *
 * Logcat 是环形缓冲，重启即空；而"这条命令设备到底收到没有、回了什么"是远程排障的
 * 第一直觉（docs/DECISIONS.md「可观测性」）。落盘才有回溯能力，与服务端的
 * `command_audit` / `api_call` 呼应——同一 `command_id` 两端各留一份痕迹。
 *
 * ## 形状
 *
 * 每行一个 JSON 对象：`ts` 是设备墙上时钟（服务端已按往返校准，见 core/clock），
 * `uptime_ms` 是单调时长——两者的分工与约束见那两个类的注释。事件名与服务端同一套
 * kebab-case 风格：`command.received` / `command.duplicate` / `command.result` /
 * `gateway.connected` / `gateway.disconnected`。
 *
 * 写失败一律吞掉（[CappedLogFile] 的约定）：痕迹比不上命令本身重要。
 */
class CommandAuditLog(dir: File, private val maxBytes: Long = MAX_BYTES) {
    private val file = File(dir, FILE_NAME)

    /** 命令被受理（重复接收记 [duplicate]，见 AgentService 的幂等接收）。 */
    fun received(commandId: String, action: String, duplicate: Boolean = false) {
        write(
            JSONObject()
                .put("event", if (duplicate) "command.duplicate" else "command.received")
                .put("command_id", commandId)
                .put("action", action),
        )
    }

    /** 命令结果回报（被拒/失败/超时/成功都记，reason_code 是排查的关键）。 */
    fun result(commandId: String, status: String, reasonCode: String, elapsedMs: Long = -1) {
        val entry = JSONObject()
            .put("event", "command.result")
            .put("command_id", commandId)
            .put("status", status)
            .put("reason_code", reasonCode)
        if (elapsedMs >= 0) entry.put("elapsed_ms", elapsedMs)
        write(entry)
    }

    /** 网关链路生命周期（连接/断开与原因）。 */
    fun gateway(connected: Boolean, detail: String) {
        write(
            JSONObject()
                .put("event", if (connected) "gateway.connected" else "gateway.disconnected")
                .put("detail", detail),
        )
    }

    private fun write(entry: JSONObject) {
        entry.put("ts", Clock.nowRfc3339())
        entry.put("uptime_ms", Uptime.nowMs())
        CappedLogFile.append(file, entry.toString() + "\n", maxBytes)
    }

    companion object {
        const val FILE_NAME = "audit.log"

        /** 与 crash.log / automation.log 同一量级：留最近几轮现场，不撑爆存储。 */
        const val MAX_BYTES = 256 * 1024L
    }
}
