package com.ryanliu.ntushower.core.net

import android.content.Context
import com.ryanliu.ntushower.core.dns.DnsResolver
import com.ryanliu.ntushower.core.tls.SecureTls
import okhttp3.CipherSuite
import okhttp3.ConnectionSpec
import okhttp3.OkHttpClient
import okhttp3.TlsVersion
import java.util.concurrent.TimeUnit

/**
 * 全应用唯一的 OkHttp 工厂：TLS、DNS、超时策略在这里定一次，网关 WebSocket、
 * OTA 下载、自注册、AdbBridge 全部经它——不允任何调用点自带第二套客户端配置
 * （第二套配置就是第二套信任面与超时语义）。
 *
 * TLS 规格与超时的依据见 [SecureTls] 与 [DnsResolver] 的类注释；三个超时参数的
 * 分工：connect 管建链、read 管两次数据之间的等待、call 管一次调用的整体上限
 * （OkHttp 官方语义）。callTimeout 默认 0 = 不封顶：网关 WS 是长连接、OTA 是大
 * 文件，慢速滴漏的响应体 readTimeout 管不住；语音下载这类有界请求由调用方传值。
 */
object HttpClientFactory {

    fun create(context: Context, readTimeoutSeconds: Long = 30, callTimeoutSeconds: Long = 0): OkHttpClient {
        val tls = SecureTls(context)
        val spec = ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS)
            .tlsVersions(TlsVersion.TLS_1_2)
            .cipherSuites(
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
                CipherSuite.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
            )
            .build()
        return OkHttpClient.Builder()
            .connectionSpecs(listOf(spec))
            .dns(DnsResolver())
            .sslSocketFactory(tls.socketFactory, tls.trustManager)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSeconds, TimeUnit.SECONDS)
            .apply { if (callTimeoutSeconds > 0) callTimeout(callTimeoutSeconds, TimeUnit.SECONDS) }
            .build()
    }
}
