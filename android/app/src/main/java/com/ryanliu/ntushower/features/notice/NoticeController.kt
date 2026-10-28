package com.ryanliu.ntushower.features.notice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.doors.DoorReading
import com.ryanliu.ntushower.features.overlay.ConditionClock
import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionSettings
import com.ryanliu.ntushower.features.overlay.LocalMoment
import com.ryanliu.ntushower.features.overlay.OverlayPresenter
import com.ryanliu.ntushower.features.overlay.OverlayReason

/**
 * 屏幕提示的主线程状态机。
 *
 * 所有状态只在主线程读写；数据库读取在 AgentService.worker 上完成，[onReading] 把结果
 * 投递到主线程。判定全部委托给 [NoticeRule] 与共用的 [com.ryanliu.ntushower.features.overlay.ConditionRule]；
 * 本类只负责收集输入、驱动窗口、记录变化。
 *
 * 时间条件用 [ConditionClock] 的服务端时间轴，经设备系统时区换算成 [LocalMoment]；
 * 没有锚点时时间条件未决，按隐藏处理。占用条件只在收到完整 [DoorReading] 时参与判定。
 * 遮盖层状态每次判定现读 [coverVisible]；遮盖层显示期间提示窗被移除。
 */
// TooManyFunctions：状态机的入口、定时器与窗口驱动各自是独立小函数；合并会把线程边界
// 与重试路径藏进一个长方法，反而更难核对。
@Suppress("TooManyFunctions")
class NoticeController(context: Context, private val deviceLog: DeviceLog, private val coverVisible: () -> Boolean) {

    private val handler = Handler(Looper.getMainLooper())
    private val presenter = OverlayPresenter(NoticeOverlay(context), handler, retry = { evaluate() })

    private var lastReading: DoorReading? = null
    private var hasReading = false
    private var lastNeedsOccupancy = false
    private var lastLoggedReason: String? = null
    private var destroyed = false

    private val timeTickRunnable = Runnable { onTimeTick() }

    /** 是否收到过**完整**读数；供 AgentService 判断要不要为看门狗补读一次。 */
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

    /** 服务销毁：停止全部回调、移除窗口；之后到达的事件丢弃。 */
    fun destroy() {
        if (!MainThread.isCurrent()) {
            MainThread.post { destroy() }
            return
        }
        if (destroyed) return
        destroyed = true
        handler.removeCallbacksAndMessages(null)
        presenter.destroy()
    }

    private fun onReadingOnMain(reading: DoorReading) {
        if (destroyed) return
        // 只有完整读数才算数：读失败也置位会让看门狗的补读路径永久失活，而浴室数据库
        // 在没人洗浴时恰恰长期安静，那次失败就再没有事件来纠正了。
        hasReading = reading.complete
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
        val condition = settings.condition
        val reading = lastReading
        val occupied = if (condition.occupancyEnabled && reading?.complete == true) {
            reading.doors.count { it.isOccupied() }
        } else {
            null
        }
        val moment = if (condition.timeEnabled) ConditionClock.now() else null

        val decision = NoticeRule.decide(settings, ConditionInputs(moment, occupied, coverVisible()))
        if (decision.visible) presenter.present(settings) else presenter.hide()
        logDecision(decision.reason, occupied, moment)
    }

    private fun currentSettings(): NoticeSettings = NoticeSettings(
        enabled = RuntimeConfig.noticeEnabled,
        text = RuntimeConfig.noticeText,
        textSizeSp = RuntimeConfig.noticeTextSizeSp,
        textColor = RuntimeConfig.noticeTextColor,
        align = RuntimeConfig.noticeAlign,
        centerOffsetDp = RuntimeConfig.noticeCenterOffsetDp,
        xDp = RuntimeConfig.noticeXDp,
        yDp = RuntimeConfig.noticeYDp,
        condition = ConditionSettings(
            timeEnabled = RuntimeConfig.noticeTimeEnabled,
            timeStart = RuntimeConfig.noticeTimeStart,
            timeEnd = RuntimeConfig.noticeTimeEnd,
            weekdays = RuntimeConfig.noticeWeekdays,
            occupancyEnabled = RuntimeConfig.noticeOccupancyEnabled,
            occupancyMin = RuntimeConfig.noticeOccupancyMin,
            occupancyMax = RuntimeConfig.noticeOccupancyMax,
        ),
    )

    private fun logDecision(reason: String, occupied: Int?, moment: LocalMoment?) {
        if (reason == lastLoggedReason) return
        val detail = "reason=$reason occupied=${occupied ?: "unknown"} " +
            "weekday=${moment?.isoWeekday ?: "unknown"} minute=${moment?.minuteOfDay ?: "unknown"}"
        deviceLog.lifecycle(if (reason == OverlayReason.VISIBLE) "notice.visible" else "notice.hidden", detail)
        Log.i(TAG, "notice $reason $detail")
        lastLoggedReason = reason
    }

    private fun scheduleTimeTick() {
        if (destroyed) return
        handler.removeCallbacks(timeTickRunnable)
        if (!RuntimeConfig.noticeEnabled || !RuntimeConfig.noticeTimeEnabled) return
        handler.postDelayed(timeTickRunnable, ConditionClock.delayUntilNextMinute())
    }

    private fun onTimeTick() {
        if (destroyed) return
        evaluate()
        scheduleTimeTick()
    }

    private fun ensureMain(entry: String): Boolean {
        if (MainThread.isCurrent()) return true
        Log.w(TAG, "$entry called off the main thread; dropped")
        return false
    }

    companion object {
        private const val TAG = "NoticeController"
    }
}
