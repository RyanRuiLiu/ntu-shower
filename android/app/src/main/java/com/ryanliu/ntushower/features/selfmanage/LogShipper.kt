package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.transport.GatewayClient
import com.ryanliu.ntushower.transport.MessageTypes
import org.json.JSONObject
import java.io.File

/**
 * 设备日志上送：device.log / crash.log / ota-install.log 三份尾部。
 *
 * ## 何时送
 *
 * - 每次握手成功强制送一次：离线期间发生的事随回连到服务端；
 * - 关键事件（DeviceLog.warn/error）后由周期任务在 1 分钟内送一次；
 * - 普通变化按周期（默认 5 分钟）送，文件没变就不发；另每 30 分钟写一条健康记录，
 *   保证"设备一直安静"与"上送链路坏了"可以区分。
 *
 * 要此刻设备上的原文时走 `device.log_tail` 现读，不等上送；上送的是离线时留下的副本。
 *
 * ## 有界
 *
 * 每份至多取尾部 [TAIL_BYTES]；服务端按（设备，种类）只留最近几份。重发无害，
 * 失败也无需重试——下一轮会带着更新内容再来。
 */
object LogShipper {

    private const val TAG = "LogShipper"

    /** 单份上送的尾部大小（字节）。 */
    const val TAIL_BYTES = 64 * 1024

    private data class Stamp(val length: Long, val modified: Long)

    private val files = mapOf(
        "device" to File(AppConfig.WORK_DIR, DeviceLog.FILE_NAME),
        "crash" to File(AppConfig.WORK_DIR, AppConfig.CRASH_LOG_NAME),
        "ota" to File(AppConfig.WORK_DIR, AppConfig.OTA_INSTALL_LOG_NAME),
    )
    private val last = mutableMapOf<String, Stamp>()

    /** 握手后强制送三份；文件不存在/为空就跳过。 */
    @Synchronized
    fun ship(client: GatewayClient) {
        shipChanged(client, force = true)
    }

    /**
     * 按变化送：返回是否至少发了一份。
     *
     * `force=true` 用于关键事件与握手：即使文件戳没变也重发一次。
     * 文件戳用长度 + mtime；CappedLogFile 裁剪后长度会变小，同样是变化。
     */
    @Synchronized
    fun shipChanged(client: GatewayClient, force: Boolean = false): Boolean {
        var sent = false
        for ((kind, file) in files) {
            if (shipKind(client, kind, file, force)) sent = true
        }
        return sent
    }

    private fun shipKind(client: GatewayClient, kind: String, file: File, force: Boolean): Boolean {
        val stamp = stampOf(file)
        if (!force && last[kind] == stamp) return false
        val content = try {
            tail(file)
        } catch (e: Exception) {
            Log.w(TAG, "read $kind log failed: ${e.message}")
            return false
        }
        if (content.isBlank()) {
            last[kind] = stamp
            return false
        }
        client.send(
            MessageTypes.DEVICE_LOG_UPLOAD,
            JSONObject().put("kind", kind).put("content", content),
        )
        last[kind] = stamp
        Log.i(TAG, "$kind log shipped (${content.length} chars)")
        return true
    }

    private fun stampOf(file: File): Stamp = if (file.isFile) Stamp(file.length(), file.lastModified()) else Stamp(0, 0)

    /** 读文件尾部至多 [TAIL_BYTES] 字节；文件不存在返回空串。 */
    internal fun tail(file: File): String {
        if (!file.exists()) return ""
        val bytes = file.readBytes()
        var start = if (bytes.size <= TAIL_BYTES) 0 else bytes.size - TAIL_BYTES
        if (start > 0) {
            // 不从一个汉字/一条 JSON 的中间开始
            var newline = start
            while (newline < bytes.size && bytes[newline] != '\n'.code.toByte()) newline++
            if (newline < bytes.size) start = newline + 1
        }
        return String(bytes, start, bytes.size - start, Charsets.UTF_8)
    }
}
