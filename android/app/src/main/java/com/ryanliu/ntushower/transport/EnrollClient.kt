package com.ryanliu.ntushower.transport

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.identity.DeviceCredential
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.net.HttpClientFactory
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response
import java.io.IOException

/**
 * 设备自注册客户端：凭 APK 内置的 claim 令牌换取每台自己的接入凭据。
 *
 * 这是 fleet-provisioning-by-claim 的设备侧（AWS IoT / Azure DPS 同型）：任何设备装上
 * APK 都能自助完成注册，服务端不需要逐台预置。注册只回答「这是我们的程序」，信任位
 * 照旧由管理员在服务端档案上点——未信任设备收 `trusted=false`，业务帧被墙。
 *
 * 调用时机由 [GatewayClient] 决定：本地没有凭据、或网关以 1008 拒绝（凭据被吊销）时
 * 先注册再连接。每次注册服务端都会吊销旧凭据再签发新的（轮换语义），重装 APK、
 * 凭据丢失都能自助恢复。
 *
 * OkHttp 异步回调跑在自己的线程上，回调统一 `handler.post` 回主线程——与
 * [GatewayClient] 的线程模型一致。
 */
class EnrollClient(context: Context, private val callback: Callback) {

    interface Callback {
        /** 注册成功、凭据已落盘：上层此刻可以发起网关连接。 */
        fun onEnrolled()

        /** 注册失败（网络、服务端拒绝、claim 无效）：上层按退避重试。 */
        fun onEnrollFailed(reason: String)
    }

    private val handler = Handler(Looper.getMainLooper())
    private val deviceId = DeviceIdentity.deviceId(context)

    // 独立实例：注册是低频一次性请求，callTimeout 封顶防慢速滴漏；TLS/DNS 配置
    // 与网关同源（HttpClientFactory 单一来源）。

    private val client: OkHttpClient by lazy {
        HttpClientFactory.create(context.applicationContext, readTimeoutSeconds = 15, callTimeoutSeconds = 20)
    }

    fun enroll(context: Context) {
        val claim = BuildConfig.DEVICE_ENROLL_TOKEN
        if (claim.isEmpty()) {
            Log.e(TAG, "enroll token not configured at build time; cannot enroll")
            handler.post { callback.onEnrollFailed("claim-token-missing") }
            return
        }
        val url = "https://${BuildConfig.SERVER_DOMAIN}/api/v1/devices/enroll"
        val body = Enrollment.encodeRequest(
            deviceId = deviceId,
            appVersion = BuildConfig.APP_VERSION,
            versionCode = BuildConfig.VERSION_CODE,
            osVersion = android.os.Build.VERSION.RELEASE,
            capabilities = SupportedActions.ALL,
        ).toString().let { RequestBody.create(MediaType.parse("application/json"), it) }
        val request = Request.Builder()
            .url(url)
            .header("Authorization", "Bearer $claim")
            .post(body)
            .build()
        Log.i(TAG, "enrolling $url (device_id=$deviceId)")
        client.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onResponse(call: Call, response: Response) {
                val text = try {
                    response.body()?.string()
                } finally {
                    response.close()
                }
                val token = if (response.isSuccessful) text?.let { Enrollment.parseToken(it) } else null
                if (token == null) {
                    Log.w(TAG, "enroll rejected: code=${response.code()}")
                    handler.post { callback.onEnrollFailed("http-${response.code()}") }
                    return
                }
                DeviceCredential.write(context, token)
                Log.i(TAG, "enrolled, credential stored")
                handler.post { callback.onEnrolled() }
            }

            override fun onFailure(call: Call, e: IOException) {
                Log.e(TAG, "enroll failed: ${e.message}")
                handler.post { callback.onEnrollFailed(e.message ?: "io") }
            }
        })
    }

    companion object {
        private const val TAG = "EnrollClient"
    }
}
