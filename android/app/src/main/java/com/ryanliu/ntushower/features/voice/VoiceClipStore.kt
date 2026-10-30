package com.ryanliu.ntushower.features.voice

import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.transport.ReasonCodes
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.security.MessageDigest

/**
 * 固定音频缓存/下载。
 *
 * 缓存放在**应用私有目录**（`filesDir/voice`），不进共享存储；服务端只下发内容
 * 哈希，URL 由 `clip_id` 推出（`/voice/assets/{id}.mp3`），设备不能借参数下载任意
 * 地址。按声明长度精确收字节、校验 SHA-256，命中缓存时也校验，损坏就重下。
 *
 * 旧版本把缓存放在 `/sdcard/ntu-shower/voice`。新实例初始化时删除旧目录，并清掉
 * 私有目录里上次中断留下的 `.part` 文件；文件超上限时按最后修改时刻淘汰。
 */
class VoiceClipStore(private val clientProvider: () -> OkHttpClient, private val dir: File) {

    /** 下载才需要 HTTP 客户端；构造发生在主线程，TLS 上下文读盘推迟到第一次下载。 */
    private val client: OkHttpClient by lazy(clientProvider)

    sealed class ClipFetch {
        data class Ready(val file: File, val downloaded: Boolean) : ClipFetch()

        data class Failed(val reasonCode: String, val message: String) : ClipFetch()

        /** 调用方（播放优先/服务停止）要求中止；这不是下载失败，不应从预下载队列移除。 */
        data object Canceled : ClipFetch()
    }

    @Volatile
    private var activeCall: Call? = null

    /** 取消正在进行的下载；播放开始或服务停止时调用，避免后台预下载占住带宽。 */
    fun cancelActiveDownload() {
        activeCall?.cancel()
    }

    /** 缓存目录首次使用前的清理；由 [ensure] 在后台线程触发，避免构造期做文件 IO。 */
    private var initialized = false

    init {
        // 旧公共缓存可能有大量文件；删目录放后台，不阻塞服务启动。
        Thread({ removeLegacyCache() }, "voice-legacy-cleanup").apply { isDaemon = true }.start()
    }

    @Synchronized
    private fun initializeIfNeeded() {
        if (initialized) return
        dir.mkdirs()
        dir.listFiles { file -> file.isFile && file.name.endsWith(".part") }?.forEach { it.delete() }
        prune(dir, emptySet())
        initialized = true
    }

    private fun removeLegacyCache() {
        val legacy = File(AppConfig.VOICE_DIR)
        if (legacy.exists() && !legacy.deleteRecursively()) {
            Log.w(TAG, "legacy voice cache not fully removed: ${legacy.absolutePath}")
        }
    }

    /**
     * 取一段音频；[keep] 是本条播放清单的全部文件名，淘汰时整组保留。
     *
     * [shouldAbort] 在开头、每个读取块与落盘前检查：预下载被播放抢占时能立刻让出带宽。
     * 同一时刻只允许一个下载（方法同步），所以播放与预下载不会互相写坏临时文件。
     */
    @Synchronized
    fun ensure(
        spec: VoiceClipSpec,
        keep: Set<String> = emptySet(),
        shouldAbort: () -> Boolean = { false },
    ): ClipFetch {
        initializeIfNeeded()
        val target = File(dir, "${spec.clipId}.mp3")
        val keepNames = keep + target.name
        if (target.isFile && target.length() == spec.sizeBytes && sha256Of(target) == spec.sha256) {
            if (!shouldAbort()) prune(dir, keepNames)
            return ClipFetch.Ready(target, downloaded = false)
        }
        if (shouldAbort()) return ClipFetch.Canceled
        target.delete()

        // 下载失败立即再试一次：播报事件不会重现，一次网络抖动就是丢一条播报，而
        // 重下的代价只是几秒。校验失败同样重下——重新拿到的字节与缓存损坏无关。
        var last: ClipFetch.Failed? = null
        repeat(DOWNLOAD_ATTEMPTS) {
            if (shouldAbort()) return ClipFetch.Canceled
            when (val result = downloadFresh(spec, shouldAbort)) {
                is ClipFetch.Ready -> {
                    if (!shouldAbort()) prune(dir, keepNames)
                    return result
                }

                is ClipFetch.Failed -> last = result

                ClipFetch.Canceled -> return ClipFetch.Canceled
            }
        }
        return requireNotNull(last) { "download attempts exhausted without a result" }
    }

    /** 一次全新的下载（临时文件 → 校验 → 原子落盘）；只返回失败，成功走 [ensure] 的返回。 */
    private fun downloadFresh(spec: VoiceClipSpec, shouldAbort: () -> Boolean): ClipFetch {
        val target = File(dir, "${spec.clipId}.mp3")
        val temp = File(dir, "${spec.clipId}.part")
        temp.delete()
        val url = "https://${BuildConfig.SERVER_DOMAIN}/voice/assets/${spec.clipId}.mp3"
        val call = client.newCall(Request.Builder().url(url).build())
        activeCall = call
        try {
            call.execute().use { response ->
                val body = response.body() ?: return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, "响应为空")
                if (!response.isSuccessful) {
                    return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, "HTTP ${response.code()}")
                }
                copyExactly(body.byteStream(), temp, spec.sizeBytes, shouldAbort)
            }
        } catch (_: DownloadCanceled) {
            temp.delete()
            return ClipFetch.Canceled
        } catch (e: IOException) {
            temp.delete()
            if (shouldAbort()) return ClipFetch.Canceled
            Log.w(TAG, "voice clip download failed", e)
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, e.message ?: "下载失败")
        } catch (e: Exception) {
            temp.delete()
            Log.w(TAG, "voice clip download failed", e)
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, e.message ?: "下载失败")
        } finally {
            if (activeCall === call) activeCall = null
        }

        if (shouldAbort()) {
            temp.delete()
            return ClipFetch.Canceled
        }
        if (sha256Of(temp) != spec.sha256) {
            temp.delete()
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_INVALID, "音频校验失败")
        }
        if (!temp.renameTo(target)) {
            temp.delete()
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, "音频落盘失败")
        }
        return ClipFetch.Ready(target, downloaded = true)
    }

    private fun copyExactly(input: InputStream, target: File, expectedSize: Long, shouldAbort: () -> Boolean) {
        var written = 0L
        target.outputStream().use { out ->
            val buffer = ByteArray(COPY_BUFFER_SIZE)
            while (true) {
                checkAbort(shouldAbort)
                val read = input.read(buffer)
                if (read < 0) break
                written += read
                if (written > expectedSize) throw IllegalStateException("音频超过声明长度")
                out.write(buffer, 0, read)
            }
        }
        checkAbort(shouldAbort)
        if (written != expectedSize) throw IllegalStateException("音频长度不符")
    }

    private fun checkAbort(shouldAbort: () -> Boolean) {
        if (shouldAbort()) throw DownloadCanceled()
    }

    private class DownloadCanceled : IOException("下载已取消")

    private fun sha256Of(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(COPY_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun prune(dir: File, keep: Set<String>) {
        val files = dir.listFiles { file -> file.isFile && file.name.endsWith(".mp3") } ?: return
        val entries = files.map { VoiceClipFile(it.name, it.length(), it.lastModified()) }
        for (name in VoiceClipCachePolicy.filesToDelete(entries, MAX_CACHE_BYTES, keep)) {
            File(dir, name).delete()
        }
    }

    companion object {
        private const val TAG = "VoiceClipStore"

        /** 下载尝试次数：第二次只在第一次失败后立刻发生，总时长仍受 callTimeout 约束。 */
        private const val DOWNLOAD_ATTEMPTS = 2
        private const val COPY_BUFFER_SIZE = 64 * 1024
        private const val MAX_CACHE_BYTES = 20L * 1024 * 1024
    }
}
