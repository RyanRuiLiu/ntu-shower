package com.ryanliu.ntushower.core.tls

import android.content.Context
import com.ryanliu.ntushower.R
import org.conscrypt.Conscrypt
import java.security.KeyStore
import java.security.Security
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * 设备 TLS 配置：
 * - 系统栈（API 19）实测无 AES-GCM/CHACHA20，由内置 Conscrypt（BoringSSL）提供 AEAD；
 * - 强制 TLSv1.2：设备默认协议集是 TLS 1.0，会被现代服务端拒绝；
 * - 系统信任库缺 ISRG 根：两条信任链合并成一个 KeyStore，两者同权。
 */
class SecureTls(context: Context) {

    val trustManager: X509TrustManager = createTrustManager(context)
    val socketFactory: SSLSocketFactory = createFactory(trustManager)

    private fun createTrustManager(context: Context): X509TrustManager {
        val cf = CertificateFactory.getInstance("X.509")
        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null) }
        val roots = arrayOf(
            "isrg_root_x1" to R.raw.isrg_root_x1,
            "isrg_root_x2" to R.raw.isrg_root_x2,
        )
        for ((alias, res) in roots) {
            context.resources.openRawResource(res).use { input ->
                ks.setCertificateEntry(alias, cf.generateCertificate(input))
            }
        }
        val custom = trustManagerOf(ks)
        val system = trustManagerOf(null)
        return object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                system.checkClientTrusted(chain, authType)
            }

            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                try {
                    system.checkServerTrusted(chain, authType)
                } catch (e: CertificateException) {
                    custom.checkServerTrusted(chain, authType)
                }
            }

            override fun getAcceptedIssuers(): Array<X509Certificate> = system.acceptedIssuers
        }
    }

    private fun trustManagerOf(ks: KeyStore?): X509TrustManager {
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        tmf.init(ks)
        return tmf.trustManagers[0] as X509TrustManager
    }

    private fun createFactory(tm: X509TrustManager): SSLSocketFactory {
        // 系统栈（API 19）没有 AEAD；Conscrypt 自带 BoringSSL，提供 TLS1.2 GCM/CHACHA20。
        val provider = Conscrypt.newProvider()
        Security.insertProviderAt(provider, 1)
        val ctx = SSLContext.getInstance("TLS", provider)
        ctx.init(null, arrayOf(tm), null)
        return Tls12SocketFactory(ctx.socketFactory)
    }
}
