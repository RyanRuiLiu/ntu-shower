package com.ryanliu.ntushower.transport

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.net.HttpClientFactory
import com.ryanliu.ntushower.core.net.ReconnectBackoff
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject

/**
 * 设备网关客户端：WSS 长连接（/gateway/device）。
 *
 * 链路行为（对应 protocol/schema/）：
 * - 连接成功后首帧发 link.hello；
 * - hello_ack 带回配置（心跳间隔等）与 trusted 标志；
 * - 按配置间隔发 link.ping；服务端 ping → pong（时钟偏差测量）；
 * - 断线自动重连，指数退避（5s 起、60s 封顶）；
 * - 业务消息一律回调 onMessage 交给上层。
 */
class GatewayClient(context: Context, private val callback: Callback) {

    interface Callback {
        fun onConnected(trusted: Boolean, config: JSONObject, upgrade: JSONObject?)
        fun onDisconnected(reason: String)
        fun onMessage(envelope: Envelope)
    }

    private val handler = Handler(Looper.getMainLooper())
    private val deviceId = DeviceIdentity.deviceId(context)

    // WebSocket 长连接不设读超时；HTTP 客户端配置（DNS/TLS/套件）单一来源见 HttpClientFactory
    private val client: OkHttpClient = HttpClientFactory.create(context, readTimeoutSeconds = 0)

    private var heartbeatRunnable: Runnable? = null
    private var webSocket: WebSocket? = null

    @Volatile private var connected = false
    private var reconnectAttempt = 0
    private var reconnectScheduled = false

    fun connect() {
        if (connected) return
        val url = "wss://${BuildConfig.SERVER_DOMAIN}/gateway/device"
        Log.i(TAG, "connecting $url (device_id=$deviceId)")
        val request = Request.Builder().url(url).build()
        webSocket = client.newWebSocket(request, listener)
    }

    fun disconnect() {
        connected = false
        reconnectScheduled = false
        handler.removeCallbacksAndMessages(null)
        webSocket?.close(CLOSE_NORMAL, "service stopped")
        webSocket = null
    }

    /** 发送业务消息（信封由 MessageCodec 组装）。 */
    fun send(type: String, payload: JSONObject, replyTo: String? = null) {
        if (!connected) {
            Log.w(TAG, "cannot send $type: not connected")
            return
        }
        webSocket?.send(MessageCodec.encode(type, payload, replyTo))
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            connected = true
            Log.i(TAG, "websocket open, sending hello")
            val hello = JSONObject()
                .put("device_id", deviceId)
                .put("app_version", BuildConfig.APP_VERSION)
                .put("version_code", BuildConfig.VERSION_CODE)
                .put("os_version", android.os.Build.VERSION.RELEASE)
                .put("capabilities", JSONArray(SupportedActions.ALL.toList()))
            webSocket.send(MessageCodec.encode("link.hello", hello))
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val env = MessageCodec.decode(text)
            if (env == null) {
                Log.w(TAG, "unparseable frame dropped")
                return
            }
            when (env.type) {
                "link.ping" -> {
                    // 服务端时钟偏差测量：pong 带设备侧时间
                    send(
                        "link.pong",
                        JSONObject().put("sender_time", Clock.nowRfc3339()),
                        replyTo = env.id,
                    )
                }

                "link.hello_ack" -> {
                    reconnectAttempt = 0
                    val trusted = env.payload.optBoolean("trusted", false)
                    val config = env.payload.optJSONObject("config") ?: JSONObject()
                    RuntimeConfig.apply(config)
                    scheduleHeartbeat()
                    val upgrade = if (env.payload.has("upgrade") && !env.payload.isNull("upgrade")) {
                        env.payload.optJSONObject("upgrade")
                    } else {
                        null
                    }
                    Log.i(TAG, "hello_ack trusted=$trusted upgrade=${upgrade?.optString("version")}")
                    handler.post { callback.onConnected(trusted, config, upgrade) }
                }

                "link.pong" -> Unit

                // 心跳回包，链路层消化
                "link.error" -> Log.w(TAG, "server error: ${env.payload}")

                else -> handler.post { callback.onMessage(env) }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "failure: ${t.message}")
            connected = false
            handler.post { callback.onDisconnected(t.message ?: "failure") }
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            connected = false
            handler.post { callback.onDisconnected("closed: $reason") }
            scheduleReconnect()
        }
    }

    /** 连接就绪（已通过 hello 认证），可发送业务帧。 */
    fun isReady(): Boolean = connected

    /** 配置变更后重排心跳（仅取消心跳任务，不影响重连任务）。 */
    fun refreshHeartbeat() {
        heartbeatRunnable?.let { handler.removeCallbacks(it) }
        if (connected) scheduleHeartbeat()
    }

    private fun scheduleHeartbeat() {
        val runnable = Runnable {
            if (connected) {
                send("link.ping", JSONObject().put("sender_time", Clock.nowRfc3339()))
                scheduleHeartbeat()
            }
        }
        heartbeatRunnable = runnable
        handler.postDelayed(runnable, RuntimeConfig.heartbeatIntervalS * 1000L)
    }

    private fun scheduleReconnect() {
        if (reconnectScheduled || connected) return
        reconnectScheduled = true
        reconnectAttempt++
        val delay = ReconnectBackoff.delayMs(
            reconnectAttempt,
            RuntimeConfig.reconnectBaseMs,
            RuntimeConfig.reconnectMaxMs,
        )
        Log.i(TAG, "reconnect in ${delay}ms (attempt=$reconnectAttempt)")
        handler.postDelayed({
            reconnectScheduled = false
            connect()
        }, delay)
    }

    companion object {
        private const val TAG = "GatewayClient"
        private const val CLOSE_NORMAL = 1000
    }
}
