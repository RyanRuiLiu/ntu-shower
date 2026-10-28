package com.ryanliu.ntushower.features.statusline

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.threading.MainThread
import org.json.JSONObject

/**
 * 楼层状态行的主线程状态机。
 *
 * 内容由服务端经 `display.status` 推送（[onStatus]），本类只负责保存最新一份、按配置
 * 决定显隐、驱动窗口。所有状态只在主线程读写；连接事件与配置变更都投递到主线程。
 *
 * ## 什么时候隐藏
 *
 * - 配置关闭；
 * - 自动化遮盖层可见（与学生正在看的操作遮罩让位）；
 * - 还没收到过状态，或服务端推来的楼层为空；
 * - 超过 [STALE_MS] 没有再收到推送。服务端每 30 秒保活一次，三倍时长没来就是链路
 *   或汇总出了问题——继续显示会让「3F 2」停在屏幕上变成一句假话。
 *
 * 链路断开时 [onDisconnected] 立即清空并隐藏，不等过期计时。
 */
// TooManyFunctions：入口、定时器、窗口驱动各自是独立小函数；合并会把线程边界与重试
// 路径藏进一个长方法，反而更难核对。
@Suppress("TooManyFunctions")
class StatusLineController(
    context: Context,
    private val deviceLog: DeviceLog,
    private val coverVisible: () -> Boolean,
) {

    private val overlay = StatusLineOverlay(context)
    private val handler = Handler(Looper.getMainLooper())

    private var windowAdded = false
    private var appliedSettings: StatusLineSettings? = null
    private var floors: List<FloorStatus> = emptyList()
    private var hasStatus = false
    private var lastStatusAt = 0L
    private var lastLoggedReason: String? = null
    private var retryPending = false
    private var destroyed = false

    private val staleRunnable = Runnable { evaluate() }
    private val addRetryRunnable = Runnable { onAddRetry() }

    /** 配置到达（启动、hello_ack、config.update、看门狗重拉）。 */
    fun onConfigChanged() {
        if (!ensureMain("onConfigChanged")) return
        if (destroyed) return
        evaluate()
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
        retryPending = false
        overlay.destroy()
        windowAdded = overlay.isAdded
        if (windowAdded) Log.w(TAG, "status line window still attached after destroy")
        appliedSettings = null
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
        val reason = when {
            !settings.enabled -> StatusLineReason.DISABLED
            coverVisible() -> StatusLineReason.COVER_VISIBLE
            !hasStatus || settings.floors.isEmpty() -> StatusLineReason.NO_STATUS
            isStale() -> StatusLineReason.STALE
            else -> StatusLineReason.VISIBLE
        }
        if (reason == StatusLineReason.VISIBLE) {
            applyDecision(settings)
        } else {
            hide()
        }
        logDecision(reason)
        if (settings.enabled && hasStatus && !isStale()) {
            scheduleStale()
        } else {
            cancelStale()
        }
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
        floors = floors,
    )

    private fun isStale(): Boolean = Uptime.nowMs() - lastStatusAt >= STALE_MS

    private fun applyDecision(settings: StatusLineSettings) {
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
    }

    private fun hide() {
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

    private fun logDecision(reason: String) {
        if (reason == lastLoggedReason) return
        val detail = "reason=$reason floors=${floors.size}"
        deviceLog.lifecycle(if (reason == StatusLineReason.VISIBLE) "display.visible" else "display.hidden", detail)
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
        private const val TAG = "StatusLineController"

        /** 服务端 30 秒保活；三倍没来即视为过期，隐藏而不是把旧数字留在屏幕上。 */
        private const val STALE_MS = 90_000L

        private const val ADD_RETRY_MS = 30_000L
    }
}
