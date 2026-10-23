package com.ryanliu.ntushower.features.voice

import android.content.Context
import android.media.AudioManager

/**
 * 原音量落盘与补恢复。
 *
 * 设音量前先把原值用 `commit()` 落盘；播放结束恢复成功后清标记。进程被杀、OTA 安装、
 * 强制停止之后，下一次服务启动（或下一次播放前）按标记补恢复。方法加锁：启动补恢复
 * 在 worker，播放前补恢复在 voice 执行器，两个线程不能同时改。
 */
class VoiceVolumeGuard(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun hasPending(): Boolean = prefs.getBoolean(KEY_PENDING, false)

    @Synchronized
    fun savePending(original: Int): Boolean =
        prefs.edit().putBoolean(KEY_PENDING, true).putInt(KEY_ORIGINAL, original).commit()

    @Synchronized
    fun restoreIfPending(audio: AudioManager): Boolean {
        if (!hasPending()) return true
        val original = prefs.getInt(KEY_ORIGINAL, 0)
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL)
        return try {
            audio.setStreamVolume(AudioManager.STREAM_VOICE_CALL, original.coerceIn(0, max), 0)
            prefs.edit().remove(KEY_PENDING).remove(KEY_ORIGINAL).commit()
        } catch (e: Exception) {
            false
        }
    }

    @Synchronized
    fun clearPending(): Boolean = prefs.edit().remove(KEY_PENDING).remove(KEY_ORIGINAL).commit()

    companion object {
        private const val PREFS = "ntu-shower-voice"
        private const val KEY_PENDING = "pending"
        private const val KEY_ORIGINAL = "original"
    }
}
