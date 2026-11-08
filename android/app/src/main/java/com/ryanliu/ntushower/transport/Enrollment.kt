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
