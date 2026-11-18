// 设备 ROM 冻结在 Android 4.4（API 19）：TYPE_PHONE 在新版 SDK 中废弃。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.statusline

import android.content.Context
import android.graphics.Color
import android.text.Spannable
import android.text.SpannableString
import android.text.style.ForegroundColorSpan
import android.util.Log
import android.view.WindowManager
import com.ryanliu.ntushower.features.overlay.OverlayTarget
import com.ryanliu.ntushower.features.overlay.OverlayWindow

/**
 * 楼层状态行窗口：一行文字，默认居中贴在屏幕顶部。
 *
 * 与屏幕提示是两个独立窗口：各自的位置、字号、配色互不影响，可以同时出现。窗口的
 * 层级、几何与重试语义都在 [OverlayWindow] 与
 * [com.ryanliu.ntushower.features.overlay.OverlayPresenter] 里。
 *
 * 状态行用 `TYPE_PHONE`（第 3 层），只压住浴室 App；提示用 `TYPE_SYSTEM_ALERT`
 * （第 9 层），因此提示始终在状态行上方。两者都是标准应用浮层，都在系统栏之下。
 *
 * 配色只改颜色，不改文字：空闲为绿色，0 个空闲与离线/无数据为灰色；
 * 颜色全部来自配置，默认值在服务端登记表。配置里的颜色无法解析时文字回落到白色、
 * 背景回落到透明，并在设备日志留痕——代码里不另存一套调色板。
 */
class StatusLineOverlay(context: Context) : OverlayTarget<StatusLineSettings> {

    private val window = OverlayWindow(context, WindowManager.LayoutParams.TYPE_PHONE)

    override val isAdded: Boolean get() = window.isAdded

    override fun show(settings: StatusLineSettings): Boolean = window.show(style(settings))

    override fun update(settings: StatusLineSettings): Boolean = window.update(style(settings))

    override fun hide(): Boolean = window.hide()

    override fun destroy() {
        window.destroy()
    }

    private fun style(settings: StatusLineSettings): OverlayWindow.Style {
        val baseColor = parseColorSafely(settings.textColor, Color.WHITE, "text")
        val availableColor = parseColorSafely(settings.availableColor, Color.WHITE, "available")
        val unavailableColor = parseColorSafely(settings.unavailableColor, Color.WHITE, "unavailable")
        val backgroundColor = if (settings.background) {
            parseColorSafely(settings.backgroundColor, Color.TRANSPARENT, "background")
        } else {
            Color.TRANSPARENT
        }
        return OverlayWindow.Style(
            text = buildText(settings, baseColor, availableColor, unavailableColor),
            textSizeSp = settings.textSizeSp,
            textColor = baseColor,
            align = settings.align,
            centerOffsetDp = settings.centerOffsetDp,
            xDp = settings.xDp,
            yDp = settings.yDp,
            backgroundColor = backgroundColor,
            verticalPaddingDp = VERTICAL_PADDING_DP,
        )
    }

    private fun buildText(
        settings: StatusLineSettings,
        baseColor: Int,
        availableColor: Int,
        unavailableColor: Int,
    ): CharSequence {
        val parts = StatusLine.parts(settings.floors)
        val plain = parts.joinToString("") { part -> part.text }

        val spannable = SpannableString(plain)
        var start = 0
        for (part in parts) {
            val end = start + part.text.length
            spannable.setSpan(
                ForegroundColorSpan(colorFor(part.tone, baseColor, availableColor, unavailableColor)),
                start,
                end,
                Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
            start = end
        }
        return spannable
    }

    private fun colorFor(tone: StatusTone, baseColor: Int, availableColor: Int, unavailableColor: Int): Int =
        when (tone) {
            StatusTone.AVAILABLE -> availableColor
            StatusTone.EMPTY, StatusTone.UNAVAILABLE -> unavailableColor
            StatusTone.PLAIN -> baseColor
        }

    private fun parseColorSafely(value: String, safeColor: Int, label: String): Int = try {
        Color.parseColor(value)
    } catch (e: IllegalArgumentException) {
        Log.w(TAG, "invalid status line $label color: $value")
        safeColor
    }

    companion object {
        private const val TAG = "StatusLineOverlay"

        /** 文字上下各留 4dp：背景贴着字时会显得局促；左右不预留，x 的语义保持为距屏幕边缘。 */
        private const val VERTICAL_PADDING_DP = 4
    }
}
