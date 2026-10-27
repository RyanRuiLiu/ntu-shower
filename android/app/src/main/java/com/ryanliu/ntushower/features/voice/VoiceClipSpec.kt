package com.ryanliu.ntushower.features.voice

import org.json.JSONArray
import org.json.JSONObject

/**
 * 一段固定音频的身份：内容哈希、字节数、时长。音量属于整条播放清单，不在单段上。
 */
data class VoiceClipSpec(val clipId: String, val sha256: String, val sizeBytes: Long, val durationMs: Long) {
    companion object {
        private val HASH = Regex("^[0-9a-f]{64}$")
        private const val MIN_SIZE_BYTES = 1L
        private const val MAX_SIZE_BYTES = 5L * 1024 * 1024
        private const val MIN_DURATION_MS = 1L
        private const val MAX_DURATION_MS = 30_000L

        fun parse(json: JSONObject): VoiceClipSpec? {
            val clipId = json.optString("clip_id")
            val sha256 = json.optString("sha256")
            val sizeBytes = json.optLong("size_bytes", -1L)
            val durationMs = json.optLong("duration_ms", -1L)
            if (!HASH.matches(clipId) || !HASH.matches(sha256)) return null
            if (sizeBytes !in MIN_SIZE_BYTES..MAX_SIZE_BYTES) return null
            if (durationMs !in MIN_DURATION_MS..MAX_DURATION_MS) return null
            return VoiceClipSpec(clipId, sha256, sizeBytes, durationMs)
        }
    }
}

/** 播放清单的一段：固定音频或静音停顿。 */
sealed class VoiceSegment {
    data class Clip(val clip: VoiceClipSpec) : VoiceSegment()

    data class Pause(val ms: Long) : VoiceSegment()
}

/**
 * `voice.play` 的参数：按顺序播放的分段、整条播报的音量与下载预算。
 *
 * 服务端把素材/数字/停顿解析成这个清单；设备只负责下载缓存、等浴室提示音结束、
 * 按序播放。清单不内联音频，`clip_id` 决定 `/voice/assets/{id}.mp3`。下载预算是
 * 服务端按最慢链路给的硬上限，避免缺素材时无限占住语音通道。
 */
data class VoicePlaySpec(val segments: List<VoiceSegment>, val volume: Int, val downloadBudgetMs: Long) {
    companion object {
        /** 与服务端 `schemas.MAX_SEGMENTS_PER_RULE` 同源。 */
        private const val MAX_SEGMENTS = 32
        private const val DIGIT_COUNT = 10

        /** 预下载清单是规则里去重后的素材：最多 32 段 + 0–9 数字素材。 */
        private const val MAX_PREPARE_CLIPS = MAX_SEGMENTS + DIGIT_COUNT
        private const val MIN_PAUSE_MS = 100L
        private const val MAX_PAUSE_MS = 5_000L
        private const val MAX_VOLUME = 5
        private const val MIN_DOWNLOAD_BUDGET_MS = 1L

        fun parse(json: JSONObject): VoicePlaySpec? {
            val volume = json.optInt("volume", -1)
            if (volume !in 0..MAX_VOLUME) return null
            val downloadBudgetMs = json.optLong("download_budget_ms", -1L)
            if (downloadBudgetMs < MIN_DOWNLOAD_BUDGET_MS) return null
            val array = json.optJSONArray("segments") ?: return null
            if (array.length() == 0 || array.length() > MAX_SEGMENTS) return null
            val segments = ArrayList<VoiceSegment>(array.length())
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: return null
                when (item.optString("kind")) {
                    "clip" -> segments += VoiceSegment.Clip(VoiceClipSpec.parse(item) ?: return null)

                    "pause" -> {
                        val ms = item.optLong("ms", -1L)
                        if (ms !in MIN_PAUSE_MS..MAX_PAUSE_MS) return null
                        segments += VoiceSegment.Pause(ms)
                    }

                    else -> return null
                }
            }
            return VoicePlaySpec(segments, volume, downloadBudgetMs)
        }

        /** `voice.prepare` 的参数：只下载清单里的全部音频，不含停顿与音量。 */
        fun parsePrepare(json: JSONObject): List<VoiceClipSpec>? {
            val array: JSONArray = json.optJSONArray("clips") ?: return null
            if (array.length() == 0 || array.length() > MAX_PREPARE_CLIPS) return null
            val clips = ArrayList<VoiceClipSpec>(array.length())
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: return null
                clips += VoiceClipSpec.parse(item) ?: return null
            }
            return clips
        }
    }
}
