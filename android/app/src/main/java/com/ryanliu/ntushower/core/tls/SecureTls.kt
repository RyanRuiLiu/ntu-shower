package com.ryanliu.ntushower.core.tls

import android.content.Context
import com.ryanliu.ntushower.R
import org.conscrypt.Conscrypt
import java.security.KeyStore
import java.security.Security
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * 设备 TLS 信任与套接字栈。
 *
 * ## 信任面：只有一个根
 *
 * 设备只对一台服务器说话，信任面就收窄到签发它的那一个根——内置 ISRG Root X2
 * （生产链实况：叶子 → Let's Encrypt YE1 → ISRG Root YE → ISRG Root X2，单根可达）。
 * 系统信任库不参与校验：它信任的上百个 CA 与本业务没有任何关系，纳入它们等于把
 * "任意一个被系统信任的 CA 签的证书"都放进可接受面（OWASP 证书锚定语境的标准收紧）。
 * 代价要如实声明：服务端若更换签发 CA，设备必须先 OTA 换根才能重新连上——根证书
 * 随包分发，这正是它换新起来的原因。
 *
 * ## 套接字栈：Conscrypt 提供 AEAD
 *
 * 系统 TLS 栈（API 19）实测只有 CBC、无任何 AEAD，满足不了服务端纯 AEAD 要求；
 * Conscrypt 自带 BoringSSL，提供 TLS 1.2 的 GCM 与 CHACHA20。设备默认协议集是
 * TLS 1.0，[Tls12SocketFactory] 把每个套接字钉到 TLSv1.2。套件清单只声明 AEAD
 * 三件（与 nginx `ssl_ciphers` 逐字对齐），不声明 CBC——服务端也不接受 CBC。
 */
class SecureTls(context: Context) {

    val trustManager: X509TrustManager = createTrustManager(context)
    val socketFactory: SSLSocketFactory = createFactory(trustManager)

    private fun createTrustManager(context: Context): X509TrustManager {
        val cf = CertificateFactory.getInstance("X.509")
        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null) }
        context.resources.openRawResource(R.raw.isrg_root_x2).use { input ->
            ks.setCertificateEntry("isrg_root_x2", cf.generateCertificate(input))
        }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(ks)
        return tmf.trustManagers[0] as X509TrustManager
    }

    private fun createFactory(tm: X509TrustManager): SSLSocketFactory {
        val provider = Conscrypt.newProvider()
        Security.insertProviderAt(provider, 1)
        val ctx = SSLContext.getInstance("TLS", provider)
        ctx.init(null, arrayOf(tm), null)
        return Tls12SocketFactory(ctx.socketFactory)
    }
}
