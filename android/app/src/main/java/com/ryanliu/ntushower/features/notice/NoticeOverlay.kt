package com.ryanliu.ntushower.features.notice

import android.content.Context
import android.graphics.Color
import android.util.Log
import com.ryanliu.ntushower.features.overlay.OverlayWindow

/**
 * 屏幕提示窗口：一行文字，盖在浴室 App 之上。
 *
 * 只负责把 [NoticeSettings] 翻译成浮层参数；显示/隐藏由 [NoticeController] 依据
 * [NoticeRule] 的结果驱动。窗口的层级、几何与重试语义都在 [OverlayWindow] 里，
 * 与楼层状态行共用同一套实现。
 *
 * 所有方法只在主线程调用；状态 `added` 只在主线程读写。
 */
class NoticeOverlay(context: Context) {

    private val window = OverlayWindow(context)

    val isAdded: Boolean get() = window.isAdded

    fun show(settings: NoticeSettings): Boolean = window.show(style(settings))

    fun update(settings: NoticeSettings): Boolean = window.update(style(settings))

    fun hide(): Boolean = window.hide()

    fun destroy() {
        window.destroy()
    }

    private fun style(settings: NoticeSettings): OverlayWindow.Style = OverlayWindow.Style(
        text = settings.text.replace('\r', ' ').replace('\n', ' ').trim(),
        textSizeSp = settings.textSizeSp,
        textColor = parseColorSafely(settings.textColor),
        align = settings.align,
        centerOffsetDp = settings.centerOffsetDp,
        xDp = settings.xDp,
        yDp = settings.yDp,
    )

    private fun parseColorSafely(value: String): Int = try {
        Color.parseColor(value)
    } catch (e: IllegalArgumentException) {
        Log.w(TAG, "invalid notice color: $value")
        Color.WHITE
    }

    companion object {
        private const val TAG = "NoticeOverlay"
    }
}
