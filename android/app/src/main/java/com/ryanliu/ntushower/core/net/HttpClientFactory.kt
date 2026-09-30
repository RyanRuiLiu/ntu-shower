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
 * 套件清单说明：OkHttp 默认白名单中 ECDHE-ECDSA 仅含 GCM，而设备（API 19）
 * 只支持 CBC——必须显式放行才能与 ECDSA 证书握手（真机实测，见 docs/ARCHITECTURE.md）。
 */
object HttpClientFactory {

    fun create(context: Context, readTimeoutSeconds: Long = 30): OkHttpClient {
        val tls = SecureTls(context)
        val spec = ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS)
            .tlsVersions(TlsVersion.TLS_1_2)
            .cipherSuites(
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA,
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA,
                CipherSuite.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
                CipherSuite.TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,
                CipherSuite.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
                CipherSuite.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
            )
            .build()
        return OkHttpClient.Builder()
            .connectionSpecs(listOf(spec))
            .dns(DnsResolver())
            .sslSocketFactory(tls.socketFactory, tls.trustManager)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(readTimeoutSeconds, TimeUnit.SECONDS)
            .build()
    }
}
