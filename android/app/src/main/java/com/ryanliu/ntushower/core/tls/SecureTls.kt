package com.ryanliu.ntushower.core.tls

import android.content.Context
import com.ryanliu.ntushower.R
import java.security.KeyStore
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * 最小现代化 TLS 配置（真机实测通过的组合，见 docs/ARCHITECTURE.md）：
 * - 强制 TLSv1.2：设备默认只启用 TLS 1.0，会被现代服务端拒绝
 * - 保持默认密码套件：实测可完成 ECDHE-ECDSA 握手，不需要旧系统的"启用全部套件"
 * - 系统信任库缺 ISRG 根：打包 ISRG X1/X2 双根，先系统信任库后回退
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
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(tm), null)
        return Tls12SocketFactory(ctx.socketFactory)
    }
}
