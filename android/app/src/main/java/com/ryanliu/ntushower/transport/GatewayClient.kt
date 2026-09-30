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
 * - hello_ack 带回配置（心跳间隔等）与 trusted 标志；[HANDSHAKE_TIMEOUT_MS] 内等不到就断掉重连；
 * - 按配置间隔发 link.ping；服务端 ping → pong（时钟偏差测量）；
 * - 断线自动重连，指数退避（5s 起、60s 封顶）；
 * - 业务消息一律回调 onMessage 交给上层。
 *
 * [connected] 与 [authenticated] 是两件事：前者是链路连上，后者是服务端已认下这台设备。
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
    private var handshakeRunnable: Runnable? = null
    private var webSocket: WebSocket? = null

    /** 链路层：WebSocket 已连上，写得进帧。 */
    @Volatile private var connected = false

    /**
     * 业务层：已收到 `link.hello_ack`，会话建立完成。
     *
     * 与 [connected] 分开是必要的——服务端要收到 `link.hello` 才会注册设备并回 ack，而那一步
     * 要写库。两者之间有一段窗口，只有一个标志的话，`isReady()` 会在设备其实还没被服务端认下
     * 来的时候就报"就绪"。
     */
    @Volatile private var authenticated = false

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
        authenticated = false
        handshakeRunnable = null
        reconnectScheduled = false
        handler.removeCallbacksAndMessages(null)
        webSocket?.close(CLOSE_NORMAL, "service stopped")
        webSocket = null
    }

    /**
     * 发送业务消息（信封由 MessageCodec 组装）。
     *
     * 判据是 [connected] 而不是 [authenticated]：这是链路层的"写得进吗"，而业务侧的时序由
     * 上层保证——[Callback.onConnected] 在 `hello_ack` 之后才触发。用后者会连带把链路帧
     * （`link.pong`）也挡住，那是握手期间就可能要发的东西。
     */
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
            authenticated = false
            Log.i(TAG, "websocket open, sending hello")
            val hello = JSONObject()
                .put("device_id", deviceId)
                .put("app_version", BuildConfig.APP_VERSION)
                .put("version_code", BuildConfig.VERSION_CODE)
                .put("os_version", android.os.Build.VERSION.RELEASE)
                .put("capabilities", JSONArray(SupportedActions.ALL.toList()))
            webSocket.send(MessageCodec.encode("link.hello", hello))
            scheduleHandshakeTimeout()
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
                    authenticated = true
                    cancelHandshakeTimeout()
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
            authenticated = false
            handler.post { callback.onDisconnected(t.message ?: "failure") }
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            connected = false
            authenticated = false
            handler.post { callback.onDisconnected("closed: $reason") }
            scheduleReconnect()
        }
    }

    /** 会话就绪（已通过 hello 认证），业务帧可以发了。 */
    fun isReady(): Boolean = authenticated

    /** 配置变更后重排心跳（仅取消心跳任务，不影响重连任务）。 */
    fun refreshHeartbeat() {
        heartbeatRunnable?.let { handler.removeCallbacks(it) }
        if (authenticated) scheduleHeartbeat()
    }

    private fun scheduleHeartbeat() {
        val runnable = Runnable {
            if (authenticated) {
                send("link.ping", JSONObject().put("sender_time", Clock.nowRfc3339()))
                scheduleHeartbeat()
            }
        }
        heartbeatRunnable = runnable
        handler.postDelayed(runnable, RuntimeConfig.heartbeatIntervalS * 1000L)
    }

    /**
     * 握手超时：超时未收到 `link.hello_ack` 就主动断掉本连接。
     *
     * 少了它会停在「链路连着、会话没建立」：心跳要等 ack 才排程，重连又被 [connected] 挡住，
     * 而服务端那边只是静静地等——设备从此不再有任何自恢复动作。
     *
     * 用关闭连接而不是直接 `scheduleReconnect`：正常收尾路径统一走 [WebSocketListener.onClosed]，
     * 断连通知与重连都在那一处，不另开一条。
     */
    private fun scheduleHandshakeTimeout() {
        val runnable = Runnable {
            if (!authenticated) {
                Log.w(TAG, "no hello_ack in ${HANDSHAKE_TIMEOUT_MS}ms, dropping connection")
                webSocket?.close(CLOSE_GOING_AWAY, "handshake timeout")
            }
        }
        handshakeRunnable = runnable
        handler.postDelayed(runnable, HANDSHAKE_TIMEOUT_MS)
    }

    private fun cancelHandshakeTimeout() {
        handshakeRunnable?.let { handler.removeCallbacks(it) }
        handshakeRunnable = null
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

        /** 标准 WebSocket 关闭码：端点主动离开。握手超时属于这一类。 */
        private const val CLOSE_GOING_AWAY = 1001

        /**
         * 等 `link.hello_ack` 的上限。
         *
         * 服务端收到 hello 后要查库、注册设备、组装 ack 再发回来，正常不到一秒。给到 20 秒是
         * 留给设备过载的余量——真机上 loadavg 长期在 7~8 之间（八核）。
         */
        private const val HANDSHAKE_TIMEOUT_MS = 20_000L
    }
}
