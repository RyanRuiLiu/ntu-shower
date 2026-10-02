package com.ryanliu.ntushower.core.log

import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.logfile.CappedLogFile
import org.json.JSONObject
import java.io.File

/**
 * 设备端统一日志：**全设备唯一**的结构化落盘入口（一个类、一个文件、一种格式）。
 *
 * ## 管什么
 *
 * Logcat 是环形缓冲、重启即空，而远程排障的第一直觉是"这台设备最近发生了什么"。
 * 凡是需要活过重启的事实——命令痕迹、网关生命周期、服务生命周期、关键错误——都
 * 经由本类落盘，用 `device.exec` 直接 `cat` 取回。各模块不再各自开日志文件。
 *
 * ## 形状
 *
 * 一行一个 JSON 对象（JSONL）：`ts` 是设备墙上时钟（服务端已按往返校准，见
 * core/clock），`uptime_ms` 是单调时长——两者的分工见那两个类的注释。事件名与
 * 服务端 `log_event` 同一套 kebab-case 风格（`command.received`、`gateway.connected`、
 * `service.started`……），两端检索语法一致。
 *
 * ## 敏感红线（硬约定）
 *
 * 学号、密码、令牌、剪贴板内容**永不入日志**——它们只允许存在于内存与系统剪贴板
 * （用后即清）。落盘的只有事件名、id、状态码与原因码这类结构性事实。
 *
 * 写失败一律吞掉（[CappedLogFile] 的约定）：日志比不上命令本身重要。
 */
class DeviceLog(dir: File, private val maxBytes: Long = CappedLogFile.DEFAULT_MAX_BYTES) {
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

    /**
     * 服务生命周期与关键状态迁移（拉起/销毁/配置变更/无障碍修复等）。
     *
     * `event` 用 kebab-case（`service.started`、`config.applied`），`detail` 是一句
     * 话的事实——只记发生了什么，不记推断。
     */
    fun lifecycle(event: String, detail: String) {
        write(
            JSONObject()
                .put("event", event)
                .put("detail", detail),
        )
    }

    /** 关键错误（catch 到的异常摘要）。堆栈细节留在 Logcat，这里记可检索的事实。 */
    fun error(event: String, detail: String) {
        write(
            JSONObject()
                .put("event", event)
                .put("detail", detail),
        )
    }

    private fun write(entry: JSONObject) {
        entry.put("ts", Clock.nowRfc3339())
        entry.put("uptime_ms", Uptime.nowMs())
        CappedLogFile.append(file, entry.toString() + "\n", maxBytes)
    }

    companion object {
        const val FILE_NAME = "device.log"
    }
}
