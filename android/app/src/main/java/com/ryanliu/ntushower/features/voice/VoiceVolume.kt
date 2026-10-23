package com.ryanliu.ntushower.features.voice

/** 一次播放的音量计划：目标值、原值、是否需要改动。 */
data class VoiceVolumePlan(val target: Int, val restore: Int, val change: Boolean)

/**
 * 播报音量计划（纯逻辑）。
 *
 * 流是 `STREAM_VOICE_CALL`：本机实测只有这条流在主输出上有非零音量（4/5），
 * Media/TTS 流为 0。目标值与原值都按该流的最大值钳制。
 */
object VoiceVolume {
    fun plan(configured: Int, current: Int, streamMax: Int): VoiceVolumePlan {
        val max = streamMax.coerceAtLeast(0)
        val target = configured.coerceIn(0, max)
        val restore = current.coerceIn(0, max)
        return VoiceVolumePlan(target, restore, target != restore)
    }
}
