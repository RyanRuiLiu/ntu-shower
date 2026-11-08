package com.ryanliu.ntushower.core.identity

import android.content.Context
import java.io.FileNotFoundException

/**
 * 设备接入凭据的本地存取：应用私有目录下的单个文件，0600 语义由 MODE_PRIVATE 保证。
 *
 * 凭据来自自注册（见 transport/EnrollClient）：设备首次连接凭 APK 内置的 claim 令牌
 * 换每台自己的接入凭据，落在这里；此后每次网关握手带上它。明文只在注册响应里出现
 * 一次，落盘之后服务端只剩摘要——丢了就重新注册（轮换语义，旧凭据自动失效）。
 *
 * 不加密、不做混淆加密：应用私有目录（/data/data/<包名>）已经隔离了其他应用，
 * 而设备本身在物理保护之内——与语音素材缓存同级的保护水位。加密的密钥总得存在
 * 本机某处，API 19 又没有 Keystore 对称密钥，自己发明一个密钥藏匿处只会更不标准。
 */
object DeviceCredential {

    private const val FILE = "gateway-token"

    fun read(context: Context): String? =
        try {
            context.openFileInput(FILE).bufferedReader().use { it.readLine()?.trim()?.takeIf { t -> t.isNotEmpty() } }
        } catch (_: FileNotFoundException) {
            null
        }

    fun write(context: Context, token: String) {
        context.openFileOutput(FILE, Context.MODE_PRIVATE).use { it.write(token.toByteArray()) }
    }

    fun clear(context: Context) {
        context.deleteFile(FILE)
    }
}
