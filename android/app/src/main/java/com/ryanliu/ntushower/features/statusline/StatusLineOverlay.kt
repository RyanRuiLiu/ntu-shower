package com.ryanliu.ntushower.features.statusline

import android.content.Context
import android.graphics.Color
import android.text.Spannable
import android.text.SpannableString
import android.text.style.ForegroundColorSpan
import android.util.Log
import com.ryanliu.ntushower.features.overlay.OverlayWindow

/**
 * 楼层状态行窗口：一行文字，默认居中贴在屏幕顶部。
 *
 * 与屏幕提示是两个独立窗口：各自的位置、字号、配色互不影响，可以同时出现。窗口的
 * 层级、几何与重试语义都在 [OverlayWindow] 里。
 *
 * 「按状态」配色只改颜色，不改文字：有空闲为绿色，0 个空闲与离线/无数据为灰色。
 * 颜色取 Material 500 的标准绿/灰——它们是语义色，不随后台主题变化。
 */
class StatusLineOverlay(context: Context) {

    private val window = OverlayWindow(context)

    val isAdded: Boolean get() = window.isAdded

    fun show(settings: StatusLineSettings): Boolean = window.show(style(settings))

    fun update(settings: StatusLineSettings): Boolean = window.update(style(settings))

    fun hide(): Boolean = window.hide()

    fun destroy() {
        window.destroy()
    }

    private fun style(settings: StatusLineSettings): OverlayWindow.Style {
        val baseColor = parseColorSafely(settings.textColor)
        return OverlayWindow.Style(
            text = buildText(settings, baseColor),
            textSizeSp = settings.textSizeSp,
            textColor = baseColor,
            align = settings.align,
            centerOffsetDp = settings.centerOffsetDp,
            xDp = settings.xDp,
            yDp = settings.yDp,
            backgroundColor = if (settings.background) BACKGROUND_COLOR else Color.TRANSPARENT,
            verticalPaddingDp = VERTICAL_PADDING_DP,
        )
    }

    private fun buildText(settings: StatusLineSettings, baseColor: Int): CharSequence {
        val parts = StatusLine.parts(settings.floors)
        val plain = parts.joinToString("") { part -> part.text }
        if (settings.colorMode != StatusLineColorMode.BY_STATE) return plain

        val spannable = SpannableString(plain)
        var start = 0
        for (part in parts) {
            val end = start + part.text.length
            spannable.setSpan(
                ForegroundColorSpan(colorFor(part.tone, baseColor)),
                start,
                end,
                Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            start = end
        }
        return spannable
    }

    private fun colorFor(tone: StatusTone, baseColor: Int): Int = when (tone) {
        StatusTone.AVAILABLE -> AVAILABLE_COLOR
        StatusTone.EMPTY, StatusTone.UNAVAILABLE -> UNAVAILABLE_COLOR
        StatusTone.PLAIN -> baseColor
    }

    private fun parseColorSafely(value: String): Int = try {
        Color.parseColor(value)
    } catch (e: IllegalArgumentException) {
        Log.w(TAG, "invalid status line color: $value")
        Color.WHITE
    }

    companion object {
        private const val TAG = "StatusLineOverlay"

        /** Material 500 的绿/灰：语义固定，不引入主题色。 */
        private const val AVAILABLE_COLOR = 0xFF4CAF50.toInt()
        private const val UNAVAILABLE_COLOR = 0xFF9E9E9E.toInt()

        /** 纯黑 60% 透明：只为压住浅色浴室界面，不抢视线。 */
        private const val BACKGROUND_COLOR = 0x99000000.toInt()

        /** 文字上下各留 4dp：背景贴着字时会显得局促；左右不预留，x 的语义保持为距屏幕边缘。 */
        private const val VERTICAL_PADDING_DP = 4
    }
}
