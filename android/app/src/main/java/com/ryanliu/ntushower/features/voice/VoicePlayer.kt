package com.ryanliu.ntushower.features.voice

import android.content.Context
import android.media.AudioManager
import android.media.MediaPlayer
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.transport.OutcomeStatus
import com.ryanliu.ntushower.transport.ReasonCodes
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** 一条语音命令的执行结果，与 `session.command_result` 的字段对应。 */
data class VoiceResult(val status: String, val reasonCode: String, val message: String, val data: JSONObject? = null)

/**
 * 播报播放器。
 *
 * 顺序固定：取缓存/下载 → 保存并设置 `STREAM_VOICE_CALL` 音量 → MediaPlayer 播放 →
 * 结束/失败/超时后恢复原音量。下载失败时还没碰音量；设置音量失败时清掉刚写的标记。
 *
 * 本机实测只有 `STREAM_VOICE_CALL` 在主输出上有非零音量（4/5），Media/TTS 流为 0。
 * 因此不调用 `setMode`、不调 `setSpeakerphoneOn`、不抢音频焦点——实测路径已经成立。
 */
class VoicePlayer(context: Context, private val store: VoiceClipStore, private val deviceLog: DeviceLog) {

    private val audio = context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val guard = VoiceVolumeGuard(context)

    @Volatile private var currentPlayer: MediaPlayer? = null

    @Volatile private var currentLatch: CountDownLatch? = null

    @Volatile private var playFailed = false

    @Volatile private var shuttingDown = false

    /** 服务启动时补恢复上一进程留下的原音量。 */
    fun restorePendingVolume() {
        if (!guard.hasPending()) return
        if (guard.restoreIfPending(audio)) {
            deviceLog.lifecycle("audio.volume-restored", "开机补恢复")
        } else {
            deviceLog.error("audio.volume-restore-failed", "开机补恢复失败")
        }
    }

    fun play(spec: VoiceClipSpec): VoiceResult {
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
        if (!guard.restoreIfPending(audio)) {
            return failed(ReasonCodes.VOICE_VOLUME_FAILED, "原音量恢复失败")
        }

        val fetch = store.ensure(spec)
        val file = when (fetch) {
            is VoiceClipStore.ClipFetch.Ready -> fetch.file
            is VoiceClipStore.ClipFetch.Failed -> return failed(fetch.reasonCode, fetch.message)
        }
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")

        val bathWaitMs = waitForBathAudio()
        if (bathWaitMs < 0) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")

        val plan = prepareVolume(spec.volume)
            ?: return failed(ReasonCodes.VOICE_VOLUME_FAILED, "设置音量失败")
        return playFile(spec, file, plan, fetch.downloaded, bathWaitMs)
    }

    /** 只下载/校验，不碰音量、不创建播放器。 */
    fun prepare(spec: VoiceClipSpec): VoiceResult {
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
        return when (val fetch = store.ensure(spec)) {
            is VoiceClipStore.ClipFetch.Ready -> {
                deviceLog.lifecycle("audio.prepared", "clip=${spec.clipId} downloaded=${fetch.downloaded}")
                success(spec, VoiceVolumePlan(spec.volume, spec.volume, false), fetch.downloaded, 0)
            }

            is VoiceClipStore.ClipFetch.Failed -> failed(fetch.reasonCode, fetch.message)
        }
    }

    /** 服务销毁：唤醒当前播放等待并停止播放器；恢复由播放线程的 finally 完成。 */
    fun shutdown() {
        shuttingDown = true
        currentLatch?.countDown()
        runCatching { currentPlayer?.stop() }
    }

    /**
     * 等浴室 App 的提示音播完。返回实际等待毫秒数；服务在等待中停止时返回 -1。
     *
     * 浴室 App 的 MediaPlayer 走 `STREAM_MUSIC`，本播放器走 `STREAM_VOICE_CALL`；
     * 两边都不抢音频焦点，同时播就会混在一起。这里只看“音乐流是否还在播”，不认
     * 具体 App——提示音结束即开口。等待放在改音量之前，避免占着音量改动等。
     */
    private fun waitForBathAudio(): Long {
        val gate = BathAudioGate(BATH_AUDIO_START_GRACE_MS, RuntimeConfig.voiceBathWaitMaxMs)
        val start = Uptime.nowMs()
        while (true) {
            if (shuttingDown) return -1
            val waited = Uptime.nowMs() - start
            when (gate.next(waited, audio.isMusicActive())) {
                BathAudioStep.PLAY -> {
                    if (waited > 0) deviceLog.lifecycle("audio.bath-wait", "waited_ms=$waited")
                    return waited
                }

                BathAudioStep.TIMEOUT -> {
                    deviceLog.warn("audio.bath-wait-timeout", "waited_ms=$waited")
                    return waited
                }

                BathAudioStep.WAIT -> {
                    try {
                        Thread.sleep(BATH_AUDIO_POLL_MS)
                    } catch (_: InterruptedException) {
                        Thread.currentThread().interrupt()
                        return -1
                    }
                }
            }
        }
    }

    private fun prepareVolume(volume: Int): VoiceVolumePlan? {
        val current = audio.getStreamVolume(AudioManager.STREAM_VOICE_CALL)
        val streamMax = audio.getStreamMaxVolume(AudioManager.STREAM_VOICE_CALL)
        val plan = VoiceVolume.plan(volume, current, streamMax)
        if (!plan.change) return plan
        if (!guard.savePending(current)) return null
        return try {
            audio.setStreamVolume(AudioManager.STREAM_VOICE_CALL, plan.target, 0)
            plan
        } catch (e: Exception) {
            guard.clearPending()
            deviceLog.error("audio.volume-set-failed", e.message ?: "")
            null
        }
    }

    @Suppress("DEPRECATION")
    private fun playFile(
        spec: VoiceClipSpec,
        file: File,
        plan: VoiceVolumePlan,
        downloaded: Boolean,
        bathWaitMs: Long,
    ): VoiceResult {
        val latch = CountDownLatch(1)
        currentLatch = latch
        playFailed = false
        val player = MediaPlayer()
        currentPlayer = player
        var outcome: VoiceResult? = null
        try {
            player.setAudioStreamType(AudioManager.STREAM_VOICE_CALL)
            player.setDataSource(file.absolutePath)
            player.setOnCompletionListener { latch.countDown() }
            player.setOnErrorListener { _, _, _ ->
                playFailed = true
                latch.countDown()
                true
            }
            player.prepare()
            player.start()
            if (!latch.await(spec.durationMs + COMPLETION_GRACE_MS, TimeUnit.MILLISECONDS)) {
                playFailed = true
            }
            if (playFailed || shuttingDown) {
                outcome = failed(
                    ReasonCodes.VOICE_PLAY_FAILED,
                    if (shuttingDown) "服务正在停止" else "播放失败或超时",
                )
            } else {
                deviceLog.lifecycle(
                    "audio.played",
                    "clip=${spec.clipId} volume=${plan.target} duration_ms=${spec.durationMs} " +
                        "bath_wait_ms=$bathWaitMs",
                )
                return success(spec, plan, downloaded, bathWaitMs)
            }
        } catch (e: Exception) {
            outcome = failed(ReasonCodes.VOICE_PLAY_FAILED, e.message ?: "播放失败")
        } finally {
            currentLatch = null
            currentPlayer = null
            runCatching { player.stop() }
            runCatching { player.release() }
            restoreVolume(plan)
        }
        val result = outcome ?: failed(ReasonCodes.VOICE_PLAY_FAILED, "播放失败")
        deviceLog.error("audio.play-failed", "clip=${spec.clipId} reason=${result.reasonCode} ${result.message}")
        return result
    }

    private fun restoreVolume(plan: VoiceVolumePlan) {
        if (!plan.change) return
        try {
            audio.setStreamVolume(AudioManager.STREAM_VOICE_CALL, plan.restore, 0)
            if (guard.clearPending()) {
                deviceLog.lifecycle("audio.volume-restored", "volume=${plan.restore}")
            } else {
                deviceLog.error("audio.volume-restore-failed", "原音量标记清除失败")
            }
        } catch (e: Exception) {
            deviceLog.error("audio.volume-restore-failed", e.message ?: "")
        }
    }

    private fun success(
        spec: VoiceClipSpec,
        plan: VoiceVolumePlan,
        downloaded: Boolean,
        bathWaitMs: Long,
    ): VoiceResult {
        val data = JSONObject()
            .put("clip_id", spec.clipId)
            .put("downloaded", downloaded)
            .put("volume", plan.target)
            .put("duration_ms", spec.durationMs)
            .put("bath_wait_ms", bathWaitMs)
        return VoiceResult(OutcomeStatus.OK, "", "", data)
    }

    private fun failed(reasonCode: String, message: String): VoiceResult =
        VoiceResult(OutcomeStatus.FAILED, reasonCode, message)

    companion object {
        private const val COMPLETION_GRACE_MS = 10_000L

        /** 浴室 App 的提示音通常与事件同时开始；等这段时间还没起来就当没有提示音。 */
        private const val BATH_AUDIO_START_GRACE_MS = 1_500L

        private const val BATH_AUDIO_POLL_MS = 250L
    }
}
