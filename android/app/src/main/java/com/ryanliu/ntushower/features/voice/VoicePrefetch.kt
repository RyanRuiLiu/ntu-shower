package com.ryanliu.ntushower.features.voice

import android.os.Process
import com.ryanliu.ntushower.core.log.DeviceLog

/**
 * 低优先级素材预下载。
 *
 * 与语音命令队列分开：预下载只下载、不播放、不占 voice 命令槽。播放开始时立即暂停
 * 并取消当前 HTTP 调用，把带宽让给真正要播的清单；播放结束后再继续。两条通路共用
 * [VoiceClipStore]，后者同一时刻只允许一个下载，播放与预下载不会写坏同一临时文件。
 *
 * 0.1 Mbps 链路上一条普通提示音可能要数秒，队列不设无限等待：每段下载完成/失败后
 * 留出间隔让网关心跳与帧回执通过；失败最多尝试一次（在 store 内），仍失败就移出队列，
 * 由下一次规则保存重新排队，不在后台无限重试。
 */
class VoicePrefetch(private val store: VoiceClipStore, private val deviceLog: DeviceLog) {

    data class ScheduleResult(val queued: Int, val dropped: Int)

    private val lock = Object()
    private val pending = LinkedHashSet<VoiceClipSpec>()
    private var allowed = false
    private var playing = false
    private var stopped = false

    private val worker = Thread(::loop, "voice-prefetch").apply {
        isDaemon = true
        start()
    }

    fun schedule(clips: List<VoiceClipSpec>): ScheduleResult = synchronized(lock) {
        if (stopped) return ScheduleResult(0, clips.size)
        var queued = 0
        var dropped = 0
        for (clip in clips) {
            if (!pending.any { it.clipId == clip.clipId }) {
                if (pending.size >= MAX_PENDING_CLIPS) {
                    dropped += 1
                } else {
                    pending.add(clip)
                    queued += 1
                }
            }
        }
        if (queued > 0) {
            deviceLog.lifecycle("audio.prefetch-queued", "queued=$queued pending=${pending.size}")
        }
        if (dropped > 0) {
            deviceLog.warn("audio.prefetch-dropped", "dropped=$dropped pending=${pending.size}")
        }
        lock.notifyAll()
        ScheduleResult(queued, dropped)
    }

    /** 网关连通且设备受信任时才允许后台下载；断开时立即让出带宽。 */
    fun setAllowed(value: Boolean) {
        val changed = synchronized(lock) {
            val previous = allowed
            allowed = value
            lock.notifyAll()
            previous != value
        }
        if (!value) store.cancelActiveDownload()
        if (changed) {
            if (value) {
                deviceLog.lifecycle("audio.prefetch-resumed", "网关已连接")
            } else {
                deviceLog.lifecycle("audio.prefetch-paused", "网关断开或未受信任")
            }
        }
    }

    /** 播放优先：开始播放时抢占带宽，结束再交还。 */
    fun setPlaying(value: Boolean) {
        synchronized(lock) {
            playing = value
            lock.notifyAll()
        }
        if (value) store.cancelActiveDownload()
    }

    fun shutdown() {
        synchronized(lock) {
            stopped = true
            lock.notifyAll()
        }
        store.cancelActiveDownload()
        worker.interrupt()
    }

    private fun loop() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_BACKGROUND)
        while (true) {
            val clip = nextClip() ?: return
            val keep = synchronized(lock) { pending.mapTo(HashSet()) { it.clipId } }
            val fetch = try {
                store.ensure(clip, keep, ::paused)
            } catch (e: Exception) {
                synchronized(lock) { pending.removeAll { it.clipId == clip.clipId } }
                deviceLog.error("audio.prefetch-error", "clip=${clip.clipId} ${e.message ?: ""}")
                gap()
                continue
            }
            when (fetch) {
                is VoiceClipStore.ClipFetch.Ready -> {
                    synchronized(lock) { pending.removeAll { it.clipId == clip.clipId } }
                    if (fetch.downloaded) {
                        deviceLog.lifecycle("audio.prepared", "clip=${clip.clipId}")
                    }
                }

                is VoiceClipStore.ClipFetch.Failed -> {
                    synchronized(lock) { pending.removeAll { it.clipId == clip.clipId } }
                    deviceLog.warn("audio.prefetch-failed", "clip=${clip.clipId} ${fetch.reasonCode} ${fetch.message}")
                }

                VoiceClipStore.ClipFetch.Canceled -> Unit
            }
            gap()
        }
    }

    private fun nextClip(): VoiceClipSpec? = synchronized(lock) {
        while (!stopped && !canWorkLocked()) {
            try {
                lock.wait()
            } catch (_: InterruptedException) {
                if (stopped) return null
                Thread.currentThread().interrupt()
                return null
            }
        }
        if (stopped) null else pending.first()
    }

    private fun canWorkLocked(): Boolean = allowed && !playing && pending.isNotEmpty()

    private fun paused(): Boolean = synchronized(lock) { stopped || !allowed || playing }

    private fun gap() {
        synchronized(lock) {
            if (stopped) return
            try {
                lock.wait(PREFETCH_GAP_MS)
            } catch (_: InterruptedException) {
                if (!stopped) Thread.currentThread().interrupt()
            }
        }
    }

    companion object {
        /**
         * 等待队列上限。一条规则最多 42 个去重素材；按单段最长 30 秒、128kbps 约
         * 480KB 估算，42 段正好落在 20MB 缓存上限内。多规则叠加时多余部分丢弃，
         * 由下次规则保存重新排队，避免缓存和内存被预下载撑爆。
         */
        private const val MAX_PENDING_CLIPS = 42

        /** 每段下载之后的让路时间：0.1 Mbps 链路上给心跳、回执与其它命令一个发送窗口。 */
        private const val PREFETCH_GAP_MS = 500L
    }
}
