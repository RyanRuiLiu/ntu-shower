package com.ryanliu.ntushower.features.voice

/**
 * 播放前的浴室提示音等待判定。
 *
 * 浴室 App 的提示音走 `STREAM_MUSIC`，本应用播报走 `STREAM_VOICE_CALL`；两边都不抢
 * 音频焦点，所以会同时响。播放前用 `AudioManager.isMusicActive()` 采样，等浴室 App
 * 播完再开口。
 *
 * 分配/结算时提示音通常先于我们的命令开始，第一次采样就能看到 active；如果我们的
 * 事件先到，最多再观察 [startGraceMs] 让提示音起来。没有提示音时也要等过这段观察
 * 窗口才播，否则会在提示音刚起时抢先开口。超过 [maxWaitMs] 仍不安静就报超时，
 * 由调用方记日志后照常播放。
 */
class BathAudioGate(private val startGraceMs: Long, private val maxWaitMs: Long) {
    private var sawMusic = false

    fun next(elapsedMs: Long, musicActive: Boolean): BathAudioStep {
        if (musicActive) {
            sawMusic = true
            return if (elapsedMs >= maxWaitMs) BathAudioStep.TIMEOUT else BathAudioStep.WAIT
        }
        if (sawMusic || elapsedMs >= startGraceMs || elapsedMs >= maxWaitMs) return BathAudioStep.PLAY
        return BathAudioStep.WAIT
    }
}

enum class BathAudioStep {
    WAIT,
    PLAY,
    TIMEOUT,
}
