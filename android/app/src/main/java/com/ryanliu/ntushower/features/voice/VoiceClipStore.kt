package com.ryanliu.ntushower.features.voice

import android.util.Log
import com.ryanliu.ntushower.BuildConfig
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.transport.ReasonCodes
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.InputStream
import java.security.MessageDigest

/**
 * 播报音频缓存/下载。
 *
 * URL 由 clip_id 推出（`https://SERVER_DOMAIN/voice/clips/{id}.mp3`），服务端不能借
 * 参数让设备去下载任意地址。按声明长度精确收字节、校验 SHA-256，命中缓存时也校验，
 * 损坏就重下。目录超上限时按最后修改时刻淘汰，当前文件永远保留。
 */
class VoiceClipStore(private val client: OkHttpClient) {

    sealed class ClipFetch {
        data class Ready(val file: File, val downloaded: Boolean) : ClipFetch()

        data class Failed(val reasonCode: String, val message: String) : ClipFetch()
    }

    fun ensure(spec: VoiceClipSpec): ClipFetch {
        val dir = File(AppConfig.VOICE_DIR).apply { mkdirs() }
        val target = File(dir, "${spec.clipId}.mp3")
        if (target.isFile && target.length() == spec.sizeBytes && sha256Of(target) == spec.sha256) {
            prune(dir, target.name)
            return ClipFetch.Ready(target, downloaded = false)
        }
        target.delete()

        val temp = File(dir, "${spec.clipId}.part")
        temp.delete()
        val url = "https://${BuildConfig.SERVER_DOMAIN}/voice/clips/${spec.clipId}.mp3"
        try {
            val request = Request.Builder().url(url).build()
            client.newCall(request).execute().use { response ->
                val body = response.body()
                    ?: return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, "响应为空")
                if (!response.isSuccessful) {
                    return ClipFetch.Failed(
                        ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED,
                        "HTTP ${response.code()}",
                    )
                }
                copyExactly(body.byteStream(), temp, spec.sizeBytes)
            }
        } catch (e: Exception) {
            temp.delete()
            Log.w(TAG, "voice clip download failed", e)
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, e.message ?: "下载失败")
        }

        if (sha256Of(temp) != spec.sha256) {
            temp.delete()
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_INVALID, "音频校验失败")
        }
        if (!temp.renameTo(target)) {
            temp.delete()
            return ClipFetch.Failed(ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED, "音频落盘失败")
        }
        prune(dir, target.name)
        return ClipFetch.Ready(target, downloaded = true)
    }

    private fun copyExactly(input: InputStream, target: File, expectedSize: Long) {
        var written = 0L
        target.outputStream().use { out ->
            val buffer = ByteArray(COPY_BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                written += read
                if (written > expectedSize) throw IllegalStateException("音频超过声明长度")
                out.write(buffer, 0, read)
            }
        }
        if (written != expectedSize) throw IllegalStateException("音频长度不符")
    }

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

    private fun prune(dir: File, keep: String) {
        val files = dir.listFiles { file -> file.isFile && file.name.endsWith(".mp3") } ?: return
        val entries = files.map { VoiceClipFile(it.name, it.length(), it.lastModified()) }
        for (name in VoiceClipCachePolicy.filesToDelete(entries, MAX_CACHE_BYTES, keep)) {
            File(dir, name).delete()
        }
    }

    companion object {
        private const val TAG = "VoiceClipStore"
        private const val COPY_BUFFER_SIZE = 64 * 1024
        private const val MAX_CACHE_BYTES = 20L * 1024 * 1024
    }
}
