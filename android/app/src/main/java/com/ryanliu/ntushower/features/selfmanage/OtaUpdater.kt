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
import java.io.InputStream
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
            // 版本号参与文件名构造，所以这里先按允许的形状校验一遍：设备这一侧自己成立，
            // 不依赖服务端先拦
            throw IllegalStateException("unexpected version format: $version")
        }
        val url = "https://${BuildConfig.SERVER_DOMAIN}${manifest.getString("url")}"
        val expectedSha256 = manifest.getString("sha256")
        val expectedSize = manifest.getLong("size")

        onProgress(version, "downloading", "")
        val dir = File(AppConfig.WORK_DIR).apply { mkdirs() }
        val target = File(dir, "update-$version.apk")
        download(url, target, expectedSize)

        onProgress(version, "verifying", "")
        val actualSha256 = sha256Of(target)
        if (!actualSha256.equals(expectedSha256, ignoreCase = true)) {
            target.delete()
            throw IllegalStateException("sha256 mismatch: $actualSha256")
        }

        onProgress(version, "installing", "")
        installDetached(target, version)
    }

    /**
     * 脱手安装：安装 → 自验证 → 未生效则强制重启进程。
     *
     * 不能用常驻通道，也不能用一次性进程：`pm install -r` 会为了替换 APK 而杀掉本
     * 进程，而那条进程是本进程的子进程，会跟着一起死——脚本跑到一半就断，结果也
     * 永远读不回来。独立进程会成为孤儿被 init 收养，把脚本跑完。
     *
     * **自验证是必须的**（真机 2026-10-01 实测）：4.4 的 `pm install -r` 会在设备
     * 处于进程被杀/存储抖动窗口时**返回 Success 但替换未生效**——旧版本继续跑，
     * 而 `pm` 的退出码与日志都是"成功"，无从分辨。这台设备物理不可达，"装了但没
     * 生效"等于升级失败且无法挽回，因此脚本自己在安装后读包版本核对，不一致就
     * `am force-stop`（root 下的标准强制收尾）并重新拉起服务。步骤全部落日志，
     * 每一步成败事后可查。
     *
     * 末尾把 APK 删掉：它是下载产物，装完就没有用了。失败也没关系，重试本来就要重下。
     */
    private fun installDetached(apk: File, expectedVersionName: String) {
        val log = File(AppConfig.WORK_DIR, INSTALL_LOG_NAME).absolutePath
        val pkg = BuildConfig.APPLICATION_ID
        RootShell.execDetached(
            "echo OTA_START > $log; " +
                // 安装；Success 只代表 installd 收下了请求，不代表生效
                "pm install -r ${apk.absolutePath} >> $log 2>&1; " +
                // 自验证：安装后实际读包版本（dumpsys 的 versionName 行）
                "installed=\$(dumpsys package $pkg 2>/dev/null | grep -m1 versionName | tr -d ' \r' ); " +
                "echo \"installed: \$installed\" >> $log; " +
                "case \"\$installed\" in *$expectedVersionName) " +
                "    echo OTA_VERIFIED >> $log;; " +
                "    *) " +
                // 未生效：强制收尾旧进程并拉起新版（am start 服务是 root 侧的标准拉起手段）
                "    echo OTA_RESTART >> $log; " +
                "    am force-stop $pkg >> $log 2>&1; " +
                "    sleep 2; " +
                "    am startservice -n $pkg/com.ryanliu.ntushower.service.AgentService >> $log 2>&1; " +
                "    echo OTA_RESTART_DONE >> $log;; " +
                "esac; " +
                "echo OTA_END >> $log; rm -f ${apk.absolutePath}",
        )
    }

    /**
     * 下载 APK，字节数由 `expectedSize` 卡死。
     *
     * 无上限地 `copyTo` 会把"服务端给了个巨大的东西"直接变成"设备磁盘写满"，那时 OTA 与门
     * 状态发件箱一起失效，而现场没有任何人能看到原因。按服务端报的大小收，多一个字节就中止
     * ——那说明这条链路上来的不是那个包。
     *
     * sha256 校验的是"内容对不对"，在下载之后；这里管的是"别先把盘写满"。
     */
    private fun download(url: String, target: File, expectedSize: Long) {
        try {
            val request = Request.Builder().url(url).build()
            client.newCall(request).execute().use { response ->
                // HTTP 非成功与响应体为空归为同一条失败：设备端对两者的处置相同（这一轮放弃、
                // 下轮重来），而状态码留在消息里够定位用
                val body = response.body()
                if (!response.isSuccessful || body == null) {
                    throw IllegalStateException("download failed: http ${response.code()}")
                }
                copyExactly(body.byteStream(), target, expectedSize)
            }
        } catch (e: Exception) {
            // 半个文件没用而空间是实打实占着的：下次重试本来就要重下，留着只是垃圾
            target.delete()
            throw e
        }
    }

    /**
     * 读干输入流并写进文件，长度必须与声明一致。
     *
     * 超了就中止：多出来的字节说明链路上来的不是那个包。少了也报错。返回时文件长度一定
     * 等于 `expectedSize`。
     */
    private fun copyExactly(input: InputStream, target: File, expectedSize: Long) {
        var written = 0L
        target.outputStream().use { out ->
            val buf = ByteArray(64 * 1024)
            var n: Int
            while (input.read(buf).also { n = it } >= 0) {
                written += n
                if (written > expectedSize) throw IllegalStateException("download longer than declared")
                out.write(buf, 0, n)
            }
        }
        if (written != expectedSize) {
            throw IllegalStateException("download truncated: $written != $expectedSize")
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
