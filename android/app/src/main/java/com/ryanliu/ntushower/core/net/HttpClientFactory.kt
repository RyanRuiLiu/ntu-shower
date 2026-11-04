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
 * 统一的 OkHttp 工厂：内置 DNS 自解析 + 最小现代化 TLS 规格。
 * 网关 WebSocket 与 OTA 下载共用同一配置，规则单一来源。
 *
 * 套件清单说明：系统 TLS 栈（API 19）实测只有 CBC、无任何 AEAD，无法满足服务端
 * 纯 AEAD 要求。SecureTls 内置 Conscrypt（BoringSSL）后，这里只声明 AEAD 套件；
 * 不保留 CBC 兜底——服务端也不再接受 CBC。
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
            // 0 = 不设整体超时：网关 WS 与 OTA 大文件沿用 readTimeout 语义。慢速滴漏的
            // 响应体 readTimeout 管不住，语音下载这类有界请求用 callTimeout 封顶
            .apply { if (callTimeoutSeconds > 0) callTimeout(callTimeoutSeconds, TimeUnit.SECONDS) }
            .build()
    }
}
