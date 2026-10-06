package com.ryanliu.ntushower.features.selfmanage

import android.content.Context
import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.identity.DeviceIdentity
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.transport.OtaContract
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.InputStream
import java.net.URLEncoder
import java.security.MessageDigest

/**
 * OTA 自更新：
 * 1. 轮询 /ota/manifest 对比 version_code（同一版本失败退避，不压秒级循环）；
 * 2. 下载新 APK 到 /sdcard/ntu-shower/ 并做 sha256 与长度校验；
 * 3. 先停进程再 root `pm install -r` 静默安装（进程存活时替换会被 ROM 静默丢弃）；
 *    安装结论只落 `pm` 的原始输出，脚本不做任何二次验证；
 * 4. 拉起服务。"新版本是否真的在跑"的唯一确认是重启后 `link.hello` 自报的
 *    `version_code`（服务端据此标记 `ota_release_install`）；真未生效时发布保持
 *    待装，按调用侧退避重装收敛。无障碍重绑由 A11yGuard 看门狗负责。
 */
class OtaUpdater(
    context: Context,
    private val client: OkHttpClient,
    private val onProgress: (version: String, stage: String, detail: String) -> Unit,
) {

    private val appContext = context.applicationContext

    /** OTA 的步骤落设备统一日志（device.log）：安装是脱手的，事后只有这里能查。 */
    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 轮询/推送共用的检查入口。
     * force 发布无视版本号（调试期反复推送同一版本）；
     * 自动化执行期间不启动，避免打断分配/结算（推送方会重试或下轮轮询覆盖）；
     * 同一版本刚尝试过安装则按 [RETRY_BACKOFF_MS] 退避——安装是尽力而为的脱手
     * 操作，失败无从确认（脚本不回传），靠"隔一段时间再试"收敛，避免把下载、
     * 安装、重启的循环压到秒级。
     *
     * 退避状态在**磁盘上**（[attemptLock] 的 mtime），不在内存：安装脚本会杀死本
     * 进程，内存里的记忆随之清零——2026-10-07 4F 实测，安装失败 + 进程重启的循环
     * 把重试压到了每 20 秒一次。跨重启的退避才能真收敛。
     */
    fun checkAndInstall(): Boolean {
        if (A11yRuntime.automationInProgress) {
            Log.i(TAG, "automation in progress, ota deferred")
            return false
        }
        return try {
            val manifest = fetchManifest() ?: return false
            if (manifest.optBoolean("up_to_date", true)) return false
            val code = manifest.optInt("version_code", 0)
            val force = manifest.optBoolean("force", false)
            val newer = code > BuildConfig.VERSION_CODE
            if (!force && !newer) return false
            val lock = attemptLock(code)
            if (lock.exists() && System.currentTimeMillis() - lock.lastModified() < RETRY_BACKOFF_MS) {
                Log.i(TAG, "same version attempted recently, backing off")
                return false
            }
            // 锁先落盘再动手：安装脚本杀死本进程后，下一次启动看到它才知道"刚试过"
            lock.writeText(versionStamp(manifest))
            applyUpdate(manifest, lock)
            true
        } catch (e: Exception) {
            Log.e(TAG, "ota check failed: ${e.message}")
            deviceLog.error("ota.check-failed", e.message ?: "")
            onProgress("", "failed", e.message ?: "")
            false
        }
    }

    /** 安装尝试锁：一个版本一个文件，mtime 即上次尝试时刻。脚本收尾时删掉它。 */
    private fun attemptLock(code: Int): File = File(AppConfig.WORK_DIR, "ota-attempt-$code.lock")

    private fun versionStamp(manifest: JSONObject): String =
        "${manifest.optString("version")} at ${Clock.wallClockRfc3339()}"

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

    private fun applyUpdate(manifest: JSONObject, lock: File) {
        val version = manifest.getString("version")
        if (!Regex(OtaContract.VERSION_PATTERN).matches(version)) {
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
        installDetached(target, version, lock)
    }

    /**
     * 脱手安装：杀进程 → 安装 → 拉起服务，日志只落命令的原始输出。
     *
     * 不能用常驻通道，也不能用一次性进程：`pm install -r` 会为了替换 APK 而杀掉本
     * 进程，而那条进程是本进程的子进程，会跟着一起死——脚本跑到一半就断，结果也
     * 永远读不回来。独立进程会成为孤儿被 init 收养，把脚本跑完。
     *
     * **pm install 必须在进程确认死亡之后执行**：本 ROM 在应用进程存活时替换 APK，
     * `pm install` 返回 Success 但替换未生效——旧版本继续跑（2026-10-02 首次实测；
     * 2026-10-07 4F 再证，force-stop 之后进程也可能仍活着，当天 0.4.32 的安装全部
     * 空转、设备陷入每 20 秒一轮的安装循环）。所以这里不用任何"停一下"的猜测：
     * agent 把**自己的 PID** 写进脚本，`am force-stop` 之后紧跟 `kill -9 <自己>`——
     * SIGKILL 即时生效，pm 运行那一刻进程必然已死。不依赖 ps/grep/awk（本 ROM 的
     * toolbox 缺前两者之外的后者，且 ps 过滤会误匹配脚本自身的命令行）。
     *
     * ## 安装结论只有一个来源：`pm install` 的原始输出
     *
     * 脚本不做二次验证（此前的 dumpsys 读 versionName 三轮重试已删）。真机实测
     * （2026-10-02，0.4.16 安装）：`pm` 已明确 Success，而 2/4/6 秒三轮读
     * `versionName` 全空、包实际已生效——本 ROM 强停换包走的路径重，安装提交后
     * `dumpsys package` 的落定窗口没有上界，轮询验证是在猜时机，只会产出假阴性
     * （那次它把成功的安装写成了 OTA_NOT_APPLIED，污染唯一的调查证据）。
     * "新版本是否真的在跑"由此只有一个权威确认：重启后 `link.hello` 自报
     * `version_code`，服务端标记安装（`ota_release_install`）——它发生在全链路
     * 终点，不存在时机问题；真未生效时发布保持待装，按调用侧退避重装收敛。
     *
     * 末尾把 APK 与尝试锁删掉：前者是下载产物，后者的使命（跨重启退避）到 pm 收尾
     * 为止。失败也没关系，重试本来就要重下。
     */
    private fun installDetached(apk: File, version: String, lock: File) {
        val log = File(AppConfig.WORK_DIR, INSTALL_LOG_NAME).absolutePath
        val pkg = BuildConfig.APPLICATION_ID
        val myPid = android.os.Process.myPid()
        // 步骤事实同步进 device.log（脱手脚本自身无法回调，这里记"已发起"）；
        // 安装的原始输出在 ota-install.log——那是脚本唯一可写的通道，也是安装
        // 结论的唯一落点（pm 的 Success/Failure [...] 原文，不二次推断）
        deviceLog.lifecycle("ota.install-started", "version=$version")
        RootShell.execDetached(
            "echo OTA_START > $log; " +
                "am force-stop $pkg >> $log 2>&1; " +
                // 自己杀自己：SIGKILL 即时生效，pm 运行时进程必然已死。kill 之后本
                // 进程已不存在，脚本是孤儿、由 init 收养继续跑完余下的命令
                "kill -9 $myPid >> $log 2>&1; " +
                // 这条输出就是安装结论，原文落盘
                "pm install -r ${apk.absolutePath} >> $log 2>&1; " +
                "am startservice -n $pkg/com.ryanliu.ntushower.service.AgentService >> $log 2>&1; " +
                "echo OTA_END >> $log; rm -f ${apk.absolutePath}; rm -f ${lock.absolutePath}",
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

        /** 同一版本安装尝试的退避窗口：窗口内不重复下载与安装。 */
        private const val RETRY_BACKOFF_MS = 5 * 60_000L
    }
}
