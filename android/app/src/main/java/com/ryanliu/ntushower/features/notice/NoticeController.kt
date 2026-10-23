package com.ryanliu.ntushower.features.notice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.core.clock.Clock
import com.ryanliu.ntushower.core.clock.ServerClock
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.doors.DoorReading
import java.util.TimeZone

/**
 * 屏幕提示的主线程状态机。
 *
 * 所有状态只在主线程读写；数据库读取在 AgentService.worker 上完成，[onReading] 把结果
 * 投递到主线程。判定全部委托给 [NoticeRule]；本类只负责收集输入、驱动窗口、记录变化。
 *
 * 时间条件用 [ServerClock] 的服务端时间轴，经设备系统时区换算成 [LocalMoment]；没有
 * 锚点时时间条件未决，按隐藏处理。占用条件只在收到完整 [DoorReading] 时参与判定。
 * 遮盖层状态每次判定现读 [coverVisible]；遮盖层显示期间提示窗被移除。
 */
// TooManyFunctions：状态机的入口、定时器、窗口驱动各自是独立小函数；合并会把线程边界
// 与重试路径藏进一个长方法，反而更难核对。
@Suppress("TooManyFunctions")
class NoticeController(context: Context, private val deviceLog: DeviceLog, private val coverVisible: () -> Boolean) {

    private val overlay = NoticeOverlay(context)
    private val handler = Handler(Looper.getMainLooper())

    private var windowAdded = false
    private var appliedSettings: NoticeSettings? = null
    private var lastReading: DoorReading? = null
    private var hasReading = false
    private var lastNeedsOccupancy = false
    private var lastLoggedDecision: NoticeDecision? = null
    private var timeTickScheduled = false
    private var retryPending = false
    private var destroyed = false

    private val timeTickRunnable = Runnable { onTimeTick() }
    private val addRetryRunnable = Runnable { onAddRetry() }

    /** 供 AgentService 在主线程判断要不要补读一次门读数。 */
    fun hasReading(): Boolean = hasReading

    /** 纯配置读取，后台线程也可安全调用（两个字段都是 volatile）。 */
    fun needsOccupancy(): Boolean = RuntimeConfig.noticeEnabled && RuntimeConfig.noticeOccupancyEnabled

    /** 配置到达（启动、hello_ack、config.update、看门狗重拉）。 */
    fun onConfigChanged() {
        if (!ensureMain("onConfigChanged")) return
        if (destroyed) return
        val needs = needsOccupancy()
        if (!lastNeedsOccupancy && needs) hasReading = false
        lastNeedsOccupancy = needs
        evaluate()
        scheduleTimeTick()
    }

    /**
     * 读数到达。可能来自 AgentService.worker；后台线程只投递，不触碰主线程状态。
     */
    fun onReading(reading: DoorReading) {
        if (MainThread.isCurrent()) {
            onReadingOnMain(reading)
        } else {
            MainThread.post { onReadingOnMain(reading) }
        }
    }

    /** 遮盖层实际显示/隐藏后由回调触发；真实状态始终从 [coverVisible] 现读。 */
    fun onCoverVisibilityChanged(visible: Boolean) {
        if (!ensureMain("onCoverVisibilityChanged")) return
        if (destroyed) return
        Log.d(TAG, "cover visibility changed: $visible")
        evaluate()
    }

    /** 服务销毁：停止两个回调、移除窗口；之后到达的事件丢弃。 */
    fun destroy() {
        if (!MainThread.isCurrent()) {
            MainThread.post { destroy() }
            return
        }
        if (destroyed) return
        destroyed = true
        handler.removeCallbacksAndMessages(null)
        timeTickScheduled = false
        retryPending = false
        overlay.destroy()
        windowAdded = overlay.isAdded
        if (windowAdded) Log.w(TAG, "notice window still attached after destroy")
        appliedSettings = null
    }

    private fun onReadingOnMain(reading: DoorReading) {
        if (destroyed) return
        hasReading = true
        lastReading = reading
        evaluate()
    }

    private fun evaluate() {
        if (destroyed) return
        try {
            evaluateInternal()
        } catch (e: Throwable) {
            Log.e(TAG, "notice evaluation failed", e)
        }
    }

    private fun evaluateInternal() {
        val settings = currentSettings()
        val reading = lastReading
        val occupied = if (settings.occupancyEnabled && hasReading && reading?.complete == true) {
            reading.doors.count { it.isOccupied() }
        } else {
            null
        }
        val moment = if (settings.timeEnabled) {
            val serverMs = ServerClock.nowRfc3339()?.let { Clock.parseRfc3339(it)?.time }
            serverMs?.let { NoticeRule.localMoment(it, TimeZone.getDefault()) }
        } else {
            null
        }

        val decision = NoticeRule.decide(settings, moment, occupied, coverVisible())
        applyDecision(settings, decision)
        logDecision(decision, occupied, moment)
    }

    private fun currentSettings(): NoticeSettings = NoticeSettings(
        enabled = RuntimeConfig.noticeEnabled,
        text = RuntimeConfig.noticeText,
        textSizeSp = RuntimeConfig.noticeTextSizeSp,
        textColor = RuntimeConfig.noticeTextColor,
        xDp = RuntimeConfig.noticeXDp,
        yDp = RuntimeConfig.noticeYDp,
        timeEnabled = RuntimeConfig.noticeTimeEnabled,
        timeStart = RuntimeConfig.noticeTimeStart,
        timeEnd = RuntimeConfig.noticeTimeEnd,
        weekdays = RuntimeConfig.noticeWeekdays,
        occupancyEnabled = RuntimeConfig.noticeOccupancyEnabled,
        occupancyMin = RuntimeConfig.noticeOccupancyMin,
        occupancyMax = RuntimeConfig.noticeOccupancyMax,
    )

    private fun applyDecision(settings: NoticeSettings, decision: NoticeDecision) {
        if (decision.visible) {
            if (!windowAdded) {
                val shown = overlay.show(settings)
                windowAdded = overlay.isAdded
                if (shown && windowAdded) {
                    appliedSettings = settings
                    cancelAddRetry()
                } else {
                    appliedSettings = null
                    scheduleAddRetry()
                }
            } else if (settings != appliedSettings) {
                val updated = overlay.update(settings)
                windowAdded = overlay.isAdded
                if (updated) {
                    appliedSettings = settings
                    cancelAddRetry()
                } else {
                    appliedSettings = null
                    scheduleAddRetry()
                }
            } else {
                cancelAddRetry()
            }
        } else {
            if (windowAdded) {
                overlay.hide()
                windowAdded = overlay.isAdded
                // 移除失败（视图仍附着）时窗口还在屏幕上；排一次重试继续尝试隐藏。
                if (windowAdded) scheduleAddRetry()
            }
            if (!windowAdded) {
                appliedSettings = null
                cancelAddRetry()
            }
        }
    }

    private fun logDecision(decision: NoticeDecision, occupied: Int?, moment: LocalMoment?) {
        if (decision == lastLoggedDecision) return
        val detail = "reason=${decision.reason} occupied=${occupied ?: "unknown"} " +
            "weekday=${moment?.isoWeekday ?: "unknown"} minute=${moment?.minuteOfDay ?: "unknown"}"
        deviceLog.lifecycle(if (decision.visible) "notice.visible" else "notice.hidden", detail)
        Log.i(TAG, "notice ${decision.reason} $detail")
        lastLoggedDecision = decision
    }

    private fun scheduleTimeTick() {
        if (destroyed) return
        handler.removeCallbacks(timeTickRunnable)
        timeTickScheduled = false
        if (!RuntimeConfig.noticeEnabled || !RuntimeConfig.noticeTimeEnabled) return

        val serverMs = ServerClock.nowRfc3339()?.let { Clock.parseRfc3339(it)?.time }
        handler.postDelayed(timeTickRunnable, NoticeRule.delayUntilNextMinute(serverMs))
        timeTickScheduled = true
    }

    private fun onTimeTick() {
        if (destroyed) return
        timeTickScheduled = false
        evaluate()
        scheduleTimeTick()
    }

    private fun scheduleAddRetry() {
        if (destroyed || retryPending) return
        retryPending = true
        handler.postDelayed(addRetryRunnable, ADD_RETRY_MS)
    }

    private fun onAddRetry() {
        if (destroyed) return
        retryPending = false
        evaluate()
    }

    private fun cancelAddRetry() {
        if (!retryPending) return
        handler.removeCallbacks(addRetryRunnable)
        retryPending = false
    }

    private fun ensureMain(entry: String): Boolean {
        if (MainThread.isCurrent()) return true
        Log.w(TAG, "$entry called off the main thread; dropped")
        return false
    }

    companion object {
        private const val TAG = "NoticeController"
        private const val ADD_RETRY_MS = 30_000L
    }
}
