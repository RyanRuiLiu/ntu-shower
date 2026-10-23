package com.ryanliu.ntushower.features.voice

import org.json.JSONObject

/**
 * 一条播报命令的参数：音频身份、下载校验值、时长与音量。
 *
 * `voice.play` 要求 volume；`voice.prepare` 只下载不播放，因此不要求 volume。
 * 参数不合法时返回 null，由调度器回报 `voice.params_invalid`，不进入下载/播放。
 */
data class VoiceClipSpec(
    val clipId: String,
    val sha256: String,
    val sizeBytes: Long,
    val durationMs: Long,
    val volume: Int,
) {
    companion object {
        private val HASH = Regex("^[0-9a-f]{64}$")
        private const val MIN_SIZE_BYTES = 1L
        private const val MAX_SIZE_BYTES = 5L * 1024 * 1024
        private const val MIN_DURATION_MS = 1L

        /** 与服务端 `tts.MAX_CLIP_DURATION_MS` 同源：合成上限 30 秒，两端必须一致。 */
        private const val MAX_DURATION_MS = 30_000L
        private const val MAX_VOLUME = 5

        fun parsePlay(json: JSONObject): VoiceClipSpec? = parse(json, requireVolume = true)

        fun parsePrepare(json: JSONObject): VoiceClipSpec? = parse(json, requireVolume = false)

        private fun parse(json: JSONObject, requireVolume: Boolean): VoiceClipSpec? {
            val clipId = json.optString("clip_id")
            val sha256 = json.optString("sha256")
            val sizeBytes = json.optLong("size_bytes", -1L)
            val durationMs = json.optLong("duration_ms", -1L)
            val volume = if (requireVolume) json.optInt("volume", -1) else 0
            if (!HASH.matches(clipId) || !HASH.matches(sha256)) return null
            if (sizeBytes !in MIN_SIZE_BYTES..MAX_SIZE_BYTES) return null
            if (durationMs !in MIN_DURATION_MS..MAX_DURATION_MS) return null
            if (volume !in 0..MAX_VOLUME) return null
            return VoiceClipSpec(clipId, sha256, sizeBytes, durationMs, volume)
        }
    }
}
