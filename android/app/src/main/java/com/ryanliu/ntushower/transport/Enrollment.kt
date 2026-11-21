package com.ryanliu.ntushower.transport

import org.json.JSONArray
import org.json.JSONObject

/**
 * 设备自注册与链路关闭码的纯逻辑：请求组装、响应解析、关闭码判定，
 * 不碰 android 类型，JVM 单测直接覆盖。
 *
 * 注册协议与服务端 `POST /api/v1/devices/enroll` 对应：Bearer 传 APK 内置的
 * claim 令牌，请求体是设备自报档案（与 link.hello 的同一组字段），响应
 * `{device_id, token}`。
 *
 * ## 关闭码：两种结局，两个码
 *
 * - [WS_CLOSE_POLICY_VIOLATION]（1008，RFC 6455 §7.4.1 的泛用策略违规码）——
 *   凭据无效或缺失：清凭据重走 claim 注册（[shouldReEnroll]）；
 * - [WS_CLOSE_DEVICE_REVOKED]（4403）——设备已停用：重注册救不回来
 *   （服务端会拒绝停用设备的注册请求），停下不再重连（[shouldStop]）。
 *   4000-4999 是 RFC 6455 §7.4.2 保留给应用私用的段，两端以这份约定解释同一个值。
 *
 * 其余关闭码与异常断连一律走普通重连：网络问题靠退避，不靠换身份。
 */
object Enrollment {

    /** RFC 6455 §7.4.1：1008 Policy Violation，端点按策略拒绝。服务端用它表示凭据无效/缺失。 */
    const val WS_CLOSE_POLICY_VIOLATION = 1008

    /**
     * 设备已停用（应用私用段，RFC 6455 §7.4.2：4000-4999 由两端约定解释）。
     * 收到它意味着服务端档案上这台设备已除名——清凭据与重注册都救不回来。
     */
    const val WS_CLOSE_DEVICE_REVOKED = 4403

    /**
     * 该关闭码是否意味着要清凭据重新走 claim 自注册（纯逻辑，JVM 可测）。
     * 只有 1008 触发；网络断连、正常关闭等一律走普通重连，不重注册。
     */
    fun shouldReEnroll(closeCode: Int): Boolean = closeCode == WS_CLOSE_POLICY_VIOLATION

    /** 该关闭码是否意味着设备已被服务端停用：停止重连，等现场人工处理。 */
    fun shouldStop(closeCode: Int): Boolean = closeCode == WS_CLOSE_DEVICE_REVOKED

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
