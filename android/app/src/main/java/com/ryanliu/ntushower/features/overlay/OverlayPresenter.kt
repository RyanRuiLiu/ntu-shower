package com.ryanliu.ntushower.features.overlay

import android.os.Handler
import android.util.Log

/** 浮层内容的最小契约；窗口的加/更/删由 [OverlayPresenter] 统一驱动。 */
interface OverlayTarget<T> {
    val isAdded: Boolean

    fun show(settings: T): Boolean

    fun update(settings: T): Boolean

    fun hide(): Boolean

    fun destroy()
}

private const val DEFAULT_RETRY_MS = 30_000L

/**
 * 一个浮层的显示状态机：加窗、原位更新、移除，以及失败后的一次重试。
 *
 * 屏幕提示与楼层状态行共用这一套——它们已经有独立的 [OverlayTarget] 与各自的显隐
 * 判定，重复的只是“窗口有没有加上、加失败了要不要再试”。重试动作由调用方给，
 * 因为重试时应当重新跑一遍完整判定（条件、内容、位置都可能已经变了），而不是拿
 * 上一份参数再提交一次。
 */
class OverlayPresenter<T>(
    private val target: OverlayTarget<T>,
    private val handler: Handler,
    private val retry: () -> Unit,
    private val retryMs: Long = DEFAULT_RETRY_MS,
) {

    private var windowAdded = false
    private var appliedSettings: T? = null
    private var retryPending = false
    private var destroyed = false
    private val retryRunnable = Runnable { onRetry() }

    /** 显示或原位更新；内容未变时不重复提交。 */
    fun present(settings: T) {
        if (destroyed) return
        if (!windowAdded) {
            val shown = target.show(settings)
            windowAdded = target.isAdded
            if (shown && windowAdded) {
                appliedSettings = settings
                cancelRetry()
            } else {
                appliedSettings = null
                scheduleRetry()
            }
        } else if (settings != appliedSettings) {
            val updated = target.update(settings)
            windowAdded = target.isAdded
            if (updated) {
                appliedSettings = settings
                cancelRetry()
            } else {
                appliedSettings = null
                scheduleRetry()
            }
        } else {
            cancelRetry()
        }
    }

    /** 移除窗口；仍附着时排一次重试继续尝试。 */
    fun hide() {
        if (destroyed) return
        if (windowAdded) {
            target.hide()
            windowAdded = target.isAdded
            if (windowAdded) scheduleRetry()
        }
        if (!windowAdded) {
            appliedSettings = null
            cancelRetry()
        }
    }

    /** 服务销毁：停止重试并尽力移除窗口；之后不再使用本实例。 */
    fun destroy() {
        if (destroyed) return
        destroyed = true
        handler.removeCallbacks(retryRunnable)
        retryPending = false
        target.destroy()
        if (target.isAdded) Log.w(TAG, "overlay still attached after destroy")
        appliedSettings = null
        windowAdded = false
    }

    private fun scheduleRetry() {
        if (destroyed || retryPending) return
        retryPending = true
        handler.postDelayed(retryRunnable, retryMs)
    }

    private fun onRetry() {
        if (destroyed) return
        retryPending = false
        retry()
    }

    private fun cancelRetry() {
        if (!retryPending) return
        handler.removeCallbacks(retryRunnable)
        retryPending = false
    }

    companion object {
        private const val TAG = "OverlayPresenter"
    }
}
