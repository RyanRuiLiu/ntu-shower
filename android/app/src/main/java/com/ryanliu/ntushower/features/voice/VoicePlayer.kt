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
 * 固定音频播放器。
 *
 * 顺序固定：下载/校验全部片段 → 等浴室提示音结束 → 保存并设置 `STREAM_VOICE_CALL`
 * 音量 → 按清单逐段播放（停顿处静音）→ 恢复原音量。下载失败时还没碰音量；设置
 * 音量失败时清掉刚写的标记。播放优先：开始下载前暂停后台预下载，缺素材时最多用
 * 服务端给的 `download_budget_ms` 下载，超时立即失败，不无限占住语音通道。
 *
 * 本机实测只有 `STREAM_VOICE_CALL` 在主输出上有非零音量（4/5），Media/TTS 流为 0。
 * 因此不调用 `setMode`、不调 `setSpeakerphoneOn`、不抢音频焦点。
 */
// 播放状态机：下载、等浴室提示音、逐段播放与音量恢复共享 currentPlayer/shutdown
// 状态，拆开会把这些状态散到多个类里。清单循环的跳转集中在这里是刻意的。
@Suppress("CyclomaticComplexMethod", "LoopWithTooManyJumpStatements", "TooManyFunctions")
class VoicePlayer(context: Context, private val store: VoiceClipStore, private val deviceLog: DeviceLog) {

    private val audio = context.applicationContext.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private val guard = VoiceVolumeGuard(context)
    private val prefetch = VoicePrefetch(store, deviceLog)

    @Volatile private var currentPlayer: MediaPlayer? = null

    @Volatile private var currentLatch: CountDownLatch? = null

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

    fun play(spec: VoicePlaySpec): VoiceResult {
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
        if (!guard.restoreIfPending(audio)) {
            return failed(ReasonCodes.VOICE_VOLUME_FAILED, "原音量恢复失败")
        }
        prefetch.setPlaying(true)
        return try {
            playWithCache(spec)
        } finally {
            prefetch.setPlaying(false)
        }
    }

    private fun playWithCache(spec: VoicePlaySpec): VoiceResult {
        val deadline = Uptime.nowMs() + spec.downloadBudgetMs
        val abort = { shuttingDown || Uptime.nowMs() >= deadline }
        val playlist = spec.segments.filterIsInstance<VoiceSegment.Clip>().mapTo(HashSet()) { it.clip.clipId }
        val files = LinkedHashMap<String, File>()
        var downloaded = false
        for (segment in spec.segments) {
            if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
            if (segment !is VoiceSegment.Clip) continue
            val clip = segment.clip
            if (files.containsKey(clip.clipId)) continue
            if (abort()) {
                return failed(
                    if (shuttingDown) ReasonCodes.VOICE_PLAY_FAILED else ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED,
                    if (shuttingDown) "服务正在停止" else "下载超时",
                )
            }
            when (val fetch = store.ensure(clip, playlist, abort)) {
                is VoiceClipStore.ClipFetch.Ready -> {
                    files[clip.clipId] = fetch.file
                    downloaded = downloaded || fetch.downloaded
                }

                is VoiceClipStore.ClipFetch.Failed -> return failed(fetch.reasonCode, fetch.message)

                VoiceClipStore.ClipFetch.Canceled -> return failed(
                    if (shuttingDown) ReasonCodes.VOICE_PLAY_FAILED else ReasonCodes.VOICE_CLIP_DOWNLOAD_FAILED,
                    if (shuttingDown) "服务正在停止" else "下载超时",
                )
            }
        }
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")

        val bathWaitMs = waitForBathAudio()
        if (bathWaitMs < 0) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")

        val plan = prepareVolume(spec.volume)
            ?: return failed(ReasonCodes.VOICE_VOLUME_FAILED, "设置音量失败")
        return playSegments(spec, files, plan, downloaded, bathWaitMs)
    }

    /** 把清单排入低优先级预下载队列；立即返回，不占用 voice 命令槽等待网络。 */
    fun prepare(clips: List<VoiceClipSpec>): VoiceResult {
        if (shuttingDown) return failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
        val unique = LinkedHashMap<String, VoiceClipSpec>()
        for (clip in clips) unique[clip.clipId] = clip
        val result = prefetch.schedule(unique.values.toList())
        return success(
            JSONObject()
                .put("clip_count", unique.size)
                .put("queued", result.queued)
                .put("dropped", result.dropped),
        )
    }

    /** 网关连通且受信任后才允许后台预下载；断开时暂停并取消当前下载。 */
    fun setPrefetchAllowed(value: Boolean) {
        prefetch.setAllowed(value)
    }

    /** 服务销毁：唤醒当前播放等待并停止播放器；恢复由播放线程的 finally 完成。 */
    fun shutdown() {
        shuttingDown = true
        prefetch.shutdown()
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

    private fun playSegments(
        spec: VoicePlaySpec,
        files: Map<String, File>,
        plan: VoiceVolumePlan,
        downloaded: Boolean,
        bathWaitMs: Long,
    ): VoiceResult {
        var outcome: VoiceResult? = null
        var played = 0
        val totalMs = spec.segments.sumOf { segment ->
            when (segment) {
                is VoiceSegment.Clip -> segment.clip.durationMs
                is VoiceSegment.Pause -> segment.ms
            }
        }
        try {
            for (segment in spec.segments) {
                if (shuttingDown) {
                    outcome = failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
                    break
                }
                when (segment) {
                    is VoiceSegment.Clip -> {
                        val file = files[segment.clip.clipId]
                        if (file == null || !playClip(segment.clip, file)) {
                            outcome = failed(
                                ReasonCodes.VOICE_PLAY_FAILED,
                                if (shuttingDown) "服务正在停止" else "播放失败或超时",
                            )
                            break
                        }
                        played += 1
                    }

                    is VoiceSegment.Pause -> {
                        if (!pause(segment.ms)) {
                            outcome = failed(ReasonCodes.VOICE_PLAY_FAILED, "服务正在停止")
                            break
                        }
                    }
                }
            }
            if (outcome == null) {
                deviceLog.lifecycle(
                    "audio.played",
                    "clip_count=$played volume=${plan.target} duration_ms=$totalMs bath_wait_ms=$bathWaitMs",
                )
                return success(
                    JSONObject()
                        .put("clip_count", played)
                        .put("downloaded", downloaded)
                        .put("volume", plan.target)
                        .put("duration_ms", totalMs)
                        .put("bath_wait_ms", bathWaitMs),
                )
            }
        } finally {
            restoreVolume(plan)
        }
        val result = outcome ?: failed(ReasonCodes.VOICE_PLAY_FAILED, "播放失败")
        deviceLog.error("audio.play-failed", "reason=${result.reasonCode} ${result.message}")
        return result
    }

    @Suppress("DEPRECATION")
    private fun playClip(clip: VoiceClipSpec, file: File): Boolean {
        val latch = CountDownLatch(1)
        currentLatch = latch
        val player = MediaPlayer()
        currentPlayer = player
        var failedThisClip = false
        try {
            player.setAudioStreamType(AudioManager.STREAM_VOICE_CALL)
            player.setDataSource(file.absolutePath)
            player.setOnCompletionListener { latch.countDown() }
            player.setOnErrorListener { _, _, _ ->
                failedThisClip = true
                latch.countDown()
                true
            }
            player.prepare()
            player.start()
            if (!latch.await(clip.durationMs + COMPLETION_GRACE_MS, TimeUnit.MILLISECONDS)) {
                failedThisClip = true
            }
            return !failedThisClip && !shuttingDown
        } catch (e: Exception) {
            deviceLog.error("audio.clip-failed", "clip=${clip.clipId} ${e.message ?: "播放失败"}")
            return false
        } finally {
            currentLatch = null
            currentPlayer = null
            runCatching { player.stop() }
            runCatching { player.release() }
        }
    }

    private fun pause(ms: Long): Boolean {
        if (ms <= 0) return true
        return try {
            Thread.sleep(ms)
            !shuttingDown
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
            false
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

    private fun success(data: JSONObject): VoiceResult = VoiceResult(OutcomeStatus.OK, "", "", data)

    private fun failed(reasonCode: String, message: String): VoiceResult =
        VoiceResult(OutcomeStatus.FAILED, reasonCode, message)

    companion object {
        private const val COMPLETION_GRACE_MS = 10_000L

        /** 浴室 App 的提示音通常与事件同时开始；等这段时间还没起来就当没有提示音。 */
        private const val BATH_AUDIO_START_GRACE_MS = 1_500L

        private const val BATH_AUDIO_POLL_MS = 250L
    }
}
