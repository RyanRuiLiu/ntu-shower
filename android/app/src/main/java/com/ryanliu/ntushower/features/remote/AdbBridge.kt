package com.ryanliu.ntushower.features.remote

import android.content.Context
import android.util.Log
import com.ryanliu.ntushower.core.net.HttpClientFactory
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.atomic.AtomicBoolean

/**
 * ADB 桥接会话（设备侧）：
 * 服务端下发 bridge.open 后，开本地 adbd TCP（root），
 * 建 WSS /gateway/bridge 二进制通道，与本地 127.0.0.1:5555 做字节互泵。
 *
 * 字节方向与语义：
 * - WS 二进制帧 → adbd：服务端转发自开发者的 adb 客户端；
 * - adbd → WS 二进制帧：adbd 响应，回传给 adb 客户端。
 * TCP 是字节流，WS 是消息流：按读到的块分帧即可，字节序不变。
 */
class AdbBridge(
    private val serverDomain: String,
    private val deviceId: String,
    private val nonce: String,
    appContext: Context,
    private val onClosed: (reason: String) -> Unit,
) {

    private val client: OkHttpClient = HttpClientFactory.create(appContext, readTimeoutSeconds = 0)

    @Volatile private var ws: WebSocket? = null

    @Volatile private var adbSocket: Socket? = null

    /** 桥接建立成功（两端都已连通）。 */
    @Volatile
    var ready = false
        private set

    private val closed = AtomicBoolean(false)

    /** 启动：先连 adbd（重试），再连桥接 WS，成功后开始互泵。 */
    fun start() {
        Thread({
            val adb = connectLocalAdb()
            if (adb == null) {
                close("Local ADB not available")
                return@Thread
            }
            adbSocket = adb
            val request = Request.Builder()
                .url("wss://$serverDomain/gateway/bridge?device_id=$deviceId&nonce=$nonce")
                .build()
            client.newWebSocket(request, listener)
        }, "adb-bridge").start()
    }

    fun close(reason: String) {
        if (!closed.compareAndSet(false, true)) return
        try {
            ws?.cancel()
        } catch (e: Exception) {
            Log.w(TAG, "cancel websocket failed: ${e.message}")
        }
        try {
            adbSocket?.close()
        } catch (e: Exception) {
            Log.w(TAG, "close adbd socket failed: ${e.message}")
        }
        onClosed(reason)
    }

    /** 连接本地 adbd：start adbd 后端口就绪需要一点时间，按旧项目实测节奏重试。 */
    private fun connectLocalAdb(): Socket? {
        for (attempt in 1..RETRY_MAX) {
            try {
                val socket = Socket()
                socket.connect(InetSocketAddress("127.0.0.1", LOCAL_ADB_PORT), 2000)
                socket.soTimeout = 0
                return socket
            } catch (e: Exception) {
                Log.w(TAG, "waiting for adbd ($attempt/$RETRY_MAX): ${e.message}")
                try {
                    Thread.sleep(RETRY_DELAY_MS)
                } catch (_: InterruptedException) {
                    return null
                }
            }
        }
        return null
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            ready = true
            Log.i(TAG, "bridge established: server <-> adbd")
            val adb = adbSocket
            if (adb == null) {
                close("adbd socket missing")
                return
            }
            pumpAdbToServer(webSocket, adb)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            try {
                adbSocket?.getOutputStream()?.let { out ->
                    synchronized(out) {
                        out.write(bytes.toByteArray())
                        out.flush()
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "write to adbd failed: ${e.message}")
                close("adbd write failed")
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            close("bridge failure: ${t.message}")
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            close("bridge closed: $reason")
        }
    }

    /** adbd → 服务端：阻塞读 socket，逐块发 WS 二进制帧。 */
    private fun pumpAdbToServer(webSocket: WebSocket, adb: java.net.Socket) {
        Thread({
            val buffer = ByteArray(BUFFER_SIZE)
            try {
                adb.getInputStream().use { input ->
                    generateSequence { input.read(buffer).takeIf { it >= 0 } }
                        .all { n -> webSocket.send(ByteString.of(buffer, 0, n)) }
                }
            } catch (e: Exception) {
                Log.w(TAG, "adbd read ended: ${e.message}")
            }
            close("adbd stream ended")
        }, "adb-pump").start()
    }

    companion object {
        private const val TAG = "AdbBridge"
        const val LOCAL_ADB_PORT = 5555
        private const val RETRY_MAX = 10
        private const val RETRY_DELAY_MS = 500L
        private const val BUFFER_SIZE = 64 * 1024
    }
}
