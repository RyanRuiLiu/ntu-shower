package com.ryanliu.ntushower.features.statusline

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.doors.DoorReading
import com.ryanliu.ntushower.features.overlay.ConditionClock
import com.ryanliu.ntushower.features.overlay.ConditionInputs
import com.ryanliu.ntushower.features.overlay.ConditionSettings
import com.ryanliu.ntushower.features.overlay.OverlayPresenter
import com.ryanliu.ntushower.features.overlay.OverlayReason
import org.json.JSONObject

/**
 * 楼层状态行的主线程状态机。
 *
 * 内容由服务端经 `status_line.update` 推送（[onStatus]），本类负责保存最新一份、收集
 * 时间/占用输入、按配置决定显隐、驱动窗口。所有状态只在主线程读写；连接事件、配置
 * 变更与读数都投递到主线程。
 *
 * ## 什么时候隐藏
 *
 * - 配置关闭；
 * - 自动化遮盖层可见；
 * - 还没收到过状态，或服务端推来的楼层为空；
 * - 超过 [STALE_MS] 没有再收到推送。服务端每 30 秒保活一次，三倍时长没来就是链路
 *   或汇总出了问题——继续显示会让「3F 2」停在屏幕上变成一句假话；
 * - 时间窗或占用条件不满足（与屏幕提示同一套 [com.ryanliu.ntushower.features.overlay.ConditionRule]）。
 *
 * 链路断开时 [onDisconnected] 立即清空并隐藏，不等过期计时。
 */
// TooManyFunctions：入口、定时器与窗口驱动各自是独立小函数；合并会把线程边界与重试
// 路径藏进一个长方法，反而更难核对。
@Suppress("TooManyFunctions")
class StatusLineController(
    context: Context,
    private val deviceLog: DeviceLog,
    private val coverVisible: () -> Boolean,
) {

    private val handler = Handler(Looper.getMainLooper())
    private val presenter = OverlayPresenter(StatusLineOverlay(context), handler, retry = { evaluate() })

    private var floors: List<FloorStatus> = emptyList()
    private var hasStatus = false
    private var lastStatusAt = 0L
    private var lastReading: DoorReading? = null
    private var hasReading = false
    private var lastNeedsOccupancy = false
    private var lastLoggedReason: String? = null
    private var destroyed = false

    private val staleRunnable = Runnable { evaluate() }
    private val timeTickRunnable = Runnable { onTimeTick() }

    /** 是否收到过**完整**读数；供 AgentService 判断要不要为看门狗补读一次。 */
    fun hasReading(): Boolean = hasReading

    /** 纯配置读取，后台线程也可安全调用（两个字段都是 volatile）。 */
    fun needsOccupancy(): Boolean = RuntimeConfig.statusLineEnabled && RuntimeConfig.statusLineOccupancyEnabled

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

    /** 服务端状态到达。携带的是整份现态，直接替换上一份。 */
    fun onStatus(payload: JSONObject) {
        if (!ensureMain("onStatus")) return
        if (destroyed) return
        floors = StatusLine.parse(payload)
        hasStatus = floors.isNotEmpty()
        lastStatusAt = Uptime.nowMs()
        evaluate()
    }

    /** 读数到达；可能来自 AgentService.worker，后台线程只投递。 */
    fun onReading(reading: DoorReading) {
        if (MainThread.isCurrent()) {
            onReadingOnMain(reading)
        } else {
            MainThread.post { onReadingOnMain(reading) }
        }
    }

    /** 网关断开：不再有新的楼层状态，立即隐藏而不是等过期。 */
    fun onDisconnected() {
        if (!ensureMain("onDisconnected")) return
        if (destroyed) return
        hasStatus = false
        floors = emptyList()
        evaluate()
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
        hasReading = reading.complete
        lastReading = reading
        evaluate()
    }

    private fun evaluate() {
        if (destroyed) return
        try {
            evaluateInternal()
        } catch (e: Throwable) {
            Log.e(TAG, "status line evaluation failed", e)
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
        val stale = isStale()

        val decision = StatusLineRule.decide(
            settings,
            ConditionInputs(moment, occupied, coverVisible()),
            hasStatus,
            stale,
        )
        if (decision.visible) presenter.present(settings) else presenter.hide()
        logDecision(decision.reason)

        if (settings.enabled && hasStatus && !stale) scheduleStale() else cancelStale()
    }

    private fun currentSettings(): StatusLineSettings = StatusLineSettings(
        enabled = RuntimeConfig.statusLineEnabled,
        textSizeSp = RuntimeConfig.statusLineTextSizeSp,
        textColor = RuntimeConfig.statusLineTextColor,
        colorMode = RuntimeConfig.statusLineColorMode,
        background = RuntimeConfig.statusLineBackground,
        align = RuntimeConfig.statusLineAlign,
        centerOffsetDp = RuntimeConfig.statusLineCenterOffsetDp,
        xDp = RuntimeConfig.statusLineXDp,
        yDp = RuntimeConfig.statusLineYDp,
        condition = ConditionSettings(
            timeEnabled = RuntimeConfig.statusLineTimeEnabled,
            timeStart = RuntimeConfig.statusLineTimeStart,
            timeEnd = RuntimeConfig.statusLineTimeEnd,
            weekdays = RuntimeConfig.statusLineWeekdays,
            occupancyEnabled = RuntimeConfig.statusLineOccupancyEnabled,
            occupancyMin = RuntimeConfig.statusLineOccupancyMin,
            occupancyMax = RuntimeConfig.statusLineOccupancyMax,
        ),
        floors = floors,
    )

    private fun isStale(): Boolean = Uptime.nowMs() - lastStatusAt >= STALE_MS

    private fun logDecision(reason: String) {
        if (reason == lastLoggedReason) return
        val detail = "reason=$reason floors=${floors.size}"
        val event = if (reason == OverlayReason.VISIBLE) "status_line.visible" else "status_line.hidden"
        deviceLog.lifecycle(event, detail)
        Log.i(TAG, "status line $detail")
        lastLoggedReason = reason
    }

    private fun scheduleStale() {
        handler.removeCallbacks(staleRunnable)
        val remaining = STALE_MS - (Uptime.nowMs() - lastStatusAt)
        if (remaining > 0) handler.postDelayed(staleRunnable, remaining)
    }

    private fun cancelStale() {
        handler.removeCallbacks(staleRunnable)
    }

    private fun scheduleTimeTick() {
        if (destroyed) return
        handler.removeCallbacks(timeTickRunnable)
        if (!RuntimeConfig.statusLineEnabled || !RuntimeConfig.statusLineTimeEnabled) return
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
        private const val TAG = "StatusLineController"

        /** 服务端 30 秒保活；三倍没来即视为过期，隐藏而不是把旧数字留在屏幕上。 */
        private const val STALE_MS = 90_000L
    }
}
