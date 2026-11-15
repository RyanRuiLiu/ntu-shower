package com.ryanliu.ntushower.transport

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.ServerClock
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.identity.DeviceCredential
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
 * - 按配置间隔发 link.ping；服务端 ping → 给 [ServerClock] 立锚点、再回 pong；
 * - 断线自动重连，指数退避加抖动（5s 起、60s 封顶；实际等待落在各级的一半到上限之间，几十台设备共用一次断网时不至于同一波次同时重连）；
 * - 业务消息一律回调 onMessage 交给上层。
 *
 * [connected] 与 [authenticated] 是两件事：前者是链路连上，后者是服务端已认下这台设备。
 *
 * ## 接入凭据与自注册
 *
 * 握手必须带设备自己的接入凭据（`Authorization: Bearer`）。本地没有凭据、或服务端以
 * [Enrollment.WS_CLOSE_POLICY_VIOLATION]（RFC 6455 1008）拒绝时，先经 [EnrollClient] 用
 * APK 内置的 claim 令牌注册一枚新凭据再连接——重装 APK、清数据都能自助恢复，不需要逐台
 * 预置。判断收敛在 [Enrollment.shouldReEnroll]。注册只回答「这是我们的程序」，信任位照旧
 * 由服务端档案回答。
 *
 * ## 一条连接，几类消息：字段该走哪一条
 *
 * 判定依据是**那个字段多久变一次**，以及接收方**需要它多新**——而不是塞进碰巧已有的那一条。
 *
 * | 通道 | 时机 | 放什么 |
 * |---|---|---|
 * | `link.hello` | 连接建立时一次 | 身份与**不变的事实**：设备 id、版本、系统、能力位、开机时刻 |
 * | `link.ping` | 周期（`heartbeat_interval_s`） | **只有时间戳** |
 * | `door.snapshot` | 喷头状态变化时 | 喷头现态（事件驱动） |
 * | `ota.progress` | 升级过程中 | 下载进度（一次性流程） |
 *
 * `link.ping` 上不放业务数据：那条通路的频率由**存活检测**决定（服务端一个间隔内收不到
 * pong 就判定链路已死），而"多久探一次活"与"多久报一次数据"本来就该能分别调。塞在一起
 * 之后，调其中一个必然连带影响另一个。
 *
 * **目前没有周期性的运行指标通道。** 内存、存储、负载这类数据要上报时不要挤进
 * `link.ping`，也不该塞进 `link.hello`（那是连接时的一次性确认，设备连续在线几天都不会
 * 重发）。该新开一条 `device.status`：设备按自己的间隔上报，服务端把它存成现态，与
 * `uptime_s` 反推出的开机时刻同属"设备健康"这一组。真要加的时候，间隔也做成配置项，
 * 默认值与范围登记在服务端的 `modules/config/spec.py`。
 */
class GatewayClient(private val context: Context, private val callback: Callback) {

    interface Callback {
        fun onConnected(trusted: Boolean, config: JSONObject, upgrade: JSONObject?, serverTime: String)
        fun onDisconnected(reason: String)
        fun onMessage(envelope: Envelope)
    }

    private val handler = Handler(Looper.getMainLooper())
    private val deviceId = DeviceIdentity.deviceId(context)

    /**
     * WebSocket 长连接不设读超时；HTTP 客户端配置（DNS/TLS/套件）单一来源见
     * [HttpClientFactory]。构造推迟到 [warmUp] 或首次 [connect]：构造发生在主线程，
     * 而 TLS 上下文要读密钥库，不该在那里建。
     */
    private val client: OkHttpClient by lazy {
        HttpClientFactory.create(context.applicationContext, readTimeoutSeconds = 0)
    }

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

    /** 注册去重：注册已在途时不再叠加，避免每帧失败都开一个注册请求。 */
    private var enrollScheduled = false

    private val enrollClient =
        EnrollClient(
            context,
            object : EnrollClient.Callback {
                override fun onEnrolled() {
                    enrollScheduled = false
                    Log.i(TAG, "credential enrolled, retrying connect")
                    connect()
                }

                override fun onEnrollFailed(reason: String) {
                    enrollScheduled = false
                    lastOfflineReason = "enroll:$reason"
                    lastOfflineAt = ServerClock.nowRfc3339() ?: Clock.wallClockRfc3339()
                    handler.post { callback.onDisconnected("enroll failed: $reason") }
                    scheduleReconnect()
                }
            },
        )

    /**
     * 显式停用标志：[disconnect] 之后不再自动重连，直到下一次 [connect]。
     *
     * 没有它时，Service 销毁（onDestroy → disconnect）触发的 onClosed 仍会排一次重连——
     * 进程活着而服务已死，一个孤儿客户端从此永远重连、永远没人消费它的回调。
     */
    @Volatile private var stopped = false

    /**
     * 上一次链路断开的原因（结构化分类）与时刻（服务端轴）。随下一次 hello 回报给
     * 服务端——「设备为什么掉线」在服务端就有结构化答案，不必现场读日志。
     *
     * 分类判据来自 OkHttp 抛出的异常类型：UnknownHostException = DNS 解析不了
     * （有线网里多半是网线/交换机/ DHCP），SSLException = TLS 层失败，ConnectException
     * 与 SocketTimeoutException = TCP 连不通或超时（路由/防火墙），onClosed = 服务端
     * 主动关闭（正常语义）。服务端轴时刻取自 [ServerClock]——它锚在服务端时间上，
     * 拿这个值做离线时段推断不会带本机钟的偏差。
     */
    @Volatile private var lastOfflineReason: String = ""

    @Volatile private var lastOfflineAt: String = ""

    private fun classifyFailure(t: Throwable): String = when (t) {
        is java.net.UnknownHostException -> "dns"
        is javax.net.ssl.SSLException -> "tls"
        is java.net.ConnectException, is java.net.SocketTimeoutException -> "tcp"
        else -> "error:${t.javaClass.simpleName}"
    }

    /** 在后台线程预建 HTTP 客户端（TLS 上下文读盘），把首次构造移出主线程。 */
    fun warmUp() {
        client.newBuilder()
    }

    fun connect() {
        stopped = false
        if (connected) return
        val url = "wss://${BuildConfig.SERVER_DOMAIN}/gateway/device"
        Log.i(TAG, "connecting $url (device_id=$deviceId)")
        val builder = Request.Builder().url(url)
        // 先试后注册：本地有凭据就带上；没有也照常发起连接。注册只由服务端的
        // 1008 拒绝触发（见 onClosed）——「没有凭据就不连接」会让设备在无需凭据
        // 的服务端形态下（如切换前的旧服务）永远进不了连接。
        DeviceCredential.read(context)?.let { builder.header("Authorization", "Bearer $it") }
        webSocket = client.newWebSocket(builder.build(), listener)
    }

    fun disconnect() {
        stopped = true
        connected = false
        authenticated = false
        handshakeRunnable = null
        heartbeatRunnable = null
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
                // 自开机以来的秒数。服务端拿它反推开机时刻——本机没有 NTP，墙上时钟的
                // 绝对值不可信；而这是个单调量，不受那个偏差影响。
                .put("uptime_s", Uptime.nowMs() / 1000)
                .put("last_offline_reason", lastOfflineReason)
                .put("last_offline_at", lastOfflineAt)
            webSocket.send(MessageCodec.encode(MessageTypes.LINK_HELLO, hello))
            scheduleHandshakeTimeout()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val env = MessageCodec.decode(text)
            if (env == null) {
                Log.w(TAG, "unparseable frame dropped")
                return
            }
            when (env.type) {
                MessageTypes.LINK_PING -> {
                    // 三件事，顺序不能反：先用这一轮的服务端时刻刷新锚点，再回一个带着
                    // 本机墙上时刻的 pong 报活。帧的产生时刻取自锚点，锚点越新越准。
                    //
                    // pong 里的 `device_time` 是给服务端算偏差用的，用途与我们这边的锚点
                    // 不同：服务端要拿它把帧中的 `open_time`/`in_time` 平移到服务端时间
                    // 轴——那两个字段是"这次会话从什么时候开始"的唯一线索，而设备钟的
                    // 绝对值不可信。这里报的是**墙上时钟**（[Clock]），不是锚点：锚点本身
                    // 就是从服务端时刻推出来的，拿它去算偏差等于自己减自己。
                    ServerClock.anchor(env.payload.optString("sender_time"))
                    val deviceTime = JSONObject().put("device_time", Clock.wallClockRfc3339())
                    send(MessageTypes.LINK_PONG, deviceTime, replyTo = env.id)
                }

                MessageTypes.LINK_HELLO_ACK -> {
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
                    val serverTime = env.payload.optString("server_time")
                    // 握手应答里的服务端时刻是最早可得的锚点，先立上再回调上层：
                    // 此后的帧时刻就有据可依，不必干等到第一轮 ping。
                    ServerClock.anchor(serverTime)
                    Log.i(TAG, "hello_ack trusted=$trusted upgrade=${upgrade?.optString("version")}")
                    handler.post { callback.onConnected(trusted, config, upgrade, serverTime) }
                }

                // 服务端对心跳的应答，链路层消化（锚点已在收到 ping 请求时立过）
                MessageTypes.LINK_PONG -> Unit

                MessageTypes.LINK_ERROR -> Log.w(TAG, "server error: ${env.payload}")

                else -> handler.post { callback.onMessage(env) }
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.e(TAG, "failure: ${t.message}")
            connected = false
            authenticated = false
            lastOfflineReason = classifyFailure(t)
            lastOfflineAt = ServerClock.nowRfc3339() ?: Clock.wallClockRfc3339()
            handler.post { callback.onDisconnected(t.message ?: "failure") }
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            connected = false
            authenticated = false
            lastOfflineReason = "closed:$code"
            lastOfflineAt = ServerClock.nowRfc3339() ?: Clock.wallClockRfc3339()
            if (Enrollment.shouldReEnroll(code)) {
                // 1008 = RFC 6455 Policy Violation：服务端按凭据策略拒绝。本地这份
                // 凭据（可能压根没有——首次安装）已不被接受，清掉并走 claim 自注册
                // 领一枚新的；注册成功后由 onEnrolled 回调重新发起连接。
                // 这是 fleet provisioning by claim 的标准流程，不另设兼容分支。
                DeviceCredential.clear(context)
                Log.w(TAG, "credential rejected (1008), re-enrolling")
                handler.post { callback.onDisconnected("credential rejected") }
                if (!enrollScheduled) {
                    enrollScheduled = true
                    enrollClient.enroll(context)
                }
                return
            }
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
                send(MessageTypes.LINK_PING, JSONObject().put("sender_time", Clock.wallClockRfc3339()))
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
        if (stopped || reconnectScheduled || connected) return
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
