package com.ryanliu.ntushower.core.tls

import java.net.InetAddress
import java.net.Socket
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory

/**
 * 把每个新建套接字的协议集钉到 TLSv1.2。
 *
 * 设备 ROM 冻结在 Android 4.4，其默认启用的协议集含 TLS 1.0/1.1——这些版本现代
 * 服务端直接拒绝。OkHttp 经 [javax.net.ssl.SSLSocketFactory] 建连接时不会替我们
 * 收窄协议集，所以在工厂这一层统一钉（代理隧道也走同一组 createSocket 重载，无一
 * 漏网）。密码套件的选择交给 [SecureTls] 的 SSLContext 与 OkHttp 的 ConnectionSpec，
 * 这里只管协议版本一件事。
 */
class Tls12SocketFactory(private val delegate: SSLSocketFactory) : SSLSocketFactory() {

    override fun getDefaultCipherSuites(): Array<String> = delegate.defaultCipherSuites
    override fun getSupportedCipherSuites(): Array<String> = delegate.supportedCipherSuites

    override fun createSocket(): Socket = patch(delegate.createSocket())
    override fun createSocket(host: String, port: Int): Socket = patch(delegate.createSocket(host, port))

    override fun createSocket(host: String, port: Int, localHost: InetAddress, localPort: Int): Socket =
        patch(delegate.createSocket(host, port, localHost, localPort))

    override fun createSocket(host: InetAddress, port: Int): Socket = patch(delegate.createSocket(host, port))

    override fun createSocket(address: InetAddress, port: Int, localAddress: InetAddress, localPort: Int): Socket =
        patch(delegate.createSocket(address, port, localAddress, localPort))

    override fun createSocket(s: Socket, host: String, port: Int, autoClose: Boolean): Socket =
        patch(delegate.createSocket(s, host, port, autoClose))

    private fun patch(socket: Socket): Socket {
        if (socket is SSLSocket) {
            socket.enabledProtocols = arrayOf("TLSv1.2")
        }
        return socket
    }
}
