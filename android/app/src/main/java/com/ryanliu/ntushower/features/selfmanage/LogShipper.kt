package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.transport.GatewayClient
import com.ryanliu.ntushower.transport.MessageTypes
import org.json.JSONObject
import java.io.File

/**
 * 设备日志上送：每次握手成功后，把 device.log 与 crash.log 的**尾部**送到服务端。
 *
 * 解决的问题：设备离线期间发生的事只存在于设备上（服务端只有 on/off 时刻，没有
 * 原因），而离线中恰恰拿不到设备——2026-10-07 的 5F 就是这一类：掉线原因写在
 * device.log 里，却没有远程通道取它。上送让证据在回连那一刻自动到位，现场不再
 * 需要读文件。
 *
 * ## 有界
 *
 * 每份至多取尾部 [TAIL_BYTES]（device.log 本身 256KB 封顶，取尾足够覆盖最近的
 * 故障现场）；服务端按（设备，种类）只留最近几份。上送是现态读数不是队列：
 * 重发无害，失败也无需重试——下一次握手会带着更新的日志再来。
 */
object LogShipper {

    private const val TAG = "LogShipper"

    /** 单份上送的尾部大小（字节）。 */
    const val TAIL_BYTES = 64 * 1024

    /**
     * 握手成功（trusted）后调一次。每类日志一份 `device.log_upload`；文件不存在或
     * 为空就跳过——空日志不值得占一条消息与服务端一行记录。
     */
    fun ship(client: GatewayClient) {
        ship(client, "device", File(AppConfig.WORK_DIR, DeviceLog.FILE_NAME))
        ship(client, "crash", File(AppConfig.WORK_DIR, "crash.log"))
    }

    private fun ship(client: GatewayClient, kind: String, file: File) {
        val content = try {
            tail(file)
        } catch (e: Exception) {
            Log.w(TAG, "read $kind log failed: ${e.message}")
            return
        }
        if (content.isBlank()) return
        val sent = client.send(
            MessageTypes.DEVICE_LOG_UPLOAD,
            JSONObject().put("kind", kind).put("content", content),
        )
        Log.i(TAG, "$kind log shipped ($sent, ${content.length} chars)")
    }

    /** 读文件尾部至多 [TAIL_BYTES] 字节；文件不存在返回空串。 */
    internal fun tail(file: File): String {
        if (!file.exists()) return ""
        val bytes = file.readBytes()
        val start = if (bytes.size <= TAIL_BYTES) 0 else bytes.size - TAIL_BYTES
        return String(bytes, start, bytes.size - start, Charsets.UTF_8)
    }
}
