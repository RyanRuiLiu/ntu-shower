package com.ryanliu.ntushower.transport

import org.json.JSONArray
import org.json.JSONObject

/**
 * 设备自注册的纯逻辑：请求组装与响应解析，不碰 android 类型，JVM 单测直接覆盖。
 *
 * 协议与服务端 `POST /api/v1/devices/enroll` 对应：Bearer 传 APK 内置的 claim 令牌，
 * 请求体是设备自报档案（与 link.hello 的同一组字段），响应 `{device_id, token}`。
 */
object Enrollment {

    /** RFC 6455 §7.4.1：1008 Policy Violation，端点按策略拒绝。服务端用它表示凭据无效/缺失。 */
    const val WS_CLOSE_POLICY_VIOLATION = 1008

    /**
     * 该关闭码是否意味着要清凭据重新走 claim 自注册（纯逻辑，JVM 可测）。
     * 只有 1008 触发；网络断连、正常关闭等一律走普通重连，不重注册。
     */
    fun shouldReEnroll(closeCode: Int): Boolean = closeCode == WS_CLOSE_POLICY_VIOLATION

    fun encodeRequest(
        deviceId: String,
        appVersion: String,
        versionCode: Int,
        osVersion: String,
        capabilities: Set<String>,
    ): JSONObject = JSONObject()
        .put("device_id", deviceId)
        .put("app_version", appVersion)
        .put("version_code", versionCode)
        .put("os_version", osVersion)
        .put("capabilities", JSONArray(capabilities.toList()))

    /**
     * 从注册响应里取出接入凭据明文。
     *
     * 服务端契约：200 时 `token` 是非空字符串；拿不到就是坏响应（字段缺失、空串、
     * 非 JSON），按失败处理——绝不把空令牌落盘，那会让设备在「有文件但内容无效」的
     * 状态里绕过重新注册的自愈路径。
     */
    fun parseToken(body: String): String? = try {
        JSONObject(body).optString("token", "").takeIf { it.isNotEmpty() }
    } catch (_: Exception) {
        null
    }
}
