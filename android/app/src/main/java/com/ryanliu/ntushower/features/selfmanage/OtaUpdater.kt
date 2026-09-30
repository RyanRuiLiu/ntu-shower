package com.ryanliu.ntushower.features.selfmanage

import android.content.Context
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.service.A11yService
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.net.URLEncoder
import java.security.MessageDigest

/**
 * OTA 自更新：
 * 1. 轮询 /ota/manifest 对比 version_code；
 * 2. 下载新 APK 到 /sdcard/ntu-shower/ 并做 sha256 校验；
 * 3. root `pm install -r` 静默安装（pm 原子性保证失败不覆盖旧版）；
 * 4. 安装提交后 MY_PACKAGE_REPLACED 自动重启服务（真机验证过的路径）。
 */
class OtaUpdater(
    context: Context,
    private val client: OkHttpClient,
    private val onProgress: (version: String, stage: String, detail: String) -> Unit,
) {

    private val appContext = context.applicationContext

    /**
     * 轮询/推送共用的检查入口。
     * force 发布无视版本号（调试期反复推送同一版本）；
     * 自动化执行期间不启动，避免打断分配/结算（推送方会重试或下轮轮询覆盖）。
     */
    fun checkAndInstall(): Boolean {
        if (A11yService.automationInProgress) {
            Log.i(TAG, "automation in progress, ota deferred")
            return false
        }
        return try {
            val manifest = fetchManifest() ?: return false
            if (manifest.optBoolean("up_to_date", true)) return false
            val force = manifest.optBoolean("force", false)
            val newer = manifest.optInt("version_code", 0) > BuildConfig.VERSION_CODE
            if (!force && !newer) return false
            applyUpdate(manifest)
            true
        } catch (e: Exception) {
            Log.e(TAG, "ota check failed: ${e.message}")
            onProgress("", "failed", e.message ?: "")
            false
        }
    }

    private fun fetchManifest(): JSONObject? {
        val url = "https://${BuildConfig.SERVER_DOMAIN}/ota/manifest" +
            "?device_id=${URLEncoder.encode(deviceId(), "UTF-8")}" +
            "&version_code=${BuildConfig.VERSION_CODE}"
        val request = Request.Builder().url(url).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                Log.w(TAG, "manifest http ${response.code()}")
                return null
            }
            val body = response.body() ?: return null
            return JSONObject(body.string())
        }
    }

    private fun applyUpdate(manifest: JSONObject) {
        val version = manifest.getString("version")
        if (!VERSION_PATTERN.matches(version)) {
            // 版本号参与文件名构造：服务端已校验，此处为纵深防御——设备虽只连自家服务端，
            // 但一旦服务端配置出错，路径穿越的代价是写入任意路径
            throw IllegalStateException("unexpected version format: $version")
        }
        val url = "https://${BuildConfig.SERVER_DOMAIN}${manifest.getString("url")}"
        val expectedSha256 = manifest.getString("sha256")

        onProgress(version, "downloading", "")
        val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
        val target = File(dir, "update-$version.apk")
        download(url, target)

        onProgress(version, "verifying", "")
        val actualSha256 = sha256Of(target)
        if (!actualSha256.equals(expectedSha256, ignoreCase = true)) {
            target.delete()
            throw IllegalStateException("sha256 mismatch: $actualSha256")
        }

        onProgress(version, "installing", "")
        installDetached(target)
    }

    /**
     * 脱手安装。
     *
     * 不能用常驻通道，也不能用一次性进程：`pm install -r` 会为了替换 APK 而杀掉本
     * 进程，而那条进程是本进程的子进程，会跟着一起死——脚本跑到一半就断，结果也
     * 永远读不回来。独立进程会成为孤儿被 init 收养，把脚本跑完。
     *
     * 代价是**拿不到安装结果**：成败写进日志，升级是否生效由服务端核对设备重连时
     * 上报的版本号判断（`link.hello` 带版本），那比脚本的返回值更接近事实。
     */
    private fun installDetached(apk: File) {
        val log = File(AppConfig.WORK_DIR, INSTALL_LOG_NAME).absolutePath
        RootShell.execDetached(
            "echo OTA_START > $log; pm install -r ${apk.absolutePath} >> $log 2>&1; echo OTA_END >> $log",
        )
    }

    private fun download(url: String, target: File) {
        val request = Request.Builder().url(url).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IllegalStateException("download http ${response.code()}")
            }
            val body = response.body() ?: throw IllegalStateException("download body empty")
            target.outputStream().use { out ->
                body.byteStream().copyTo(out)
            }
        }
    }

    private fun sha256Of(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            var n: Int
            while (input.read(buf).also { n = it } >= 0) {
                digest.update(buf, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun deviceId(): String = DeviceIdentity.deviceId(appContext)

    companion object {
        private const val TAG = "OtaUpdater"

        /** 安装日志文件名（位于 `AppConfig.WORK_DIR`）。安装是脱手执行的，成败只在这里留痕。 */
        private const val INSTALL_LOG_NAME = "ota-install.log"

        /** 与服务端 `ota/service.py` 的 VERSION_PATTERN 保持一致的版本号形状。 */
        private val VERSION_PATTERN = Regex("""^\d+\.\d+\.\d+$""")
    }
}
