// 设备 ROM 冻结在 Android 4.4（API 19）：TYPE_SYSTEM_ERROR / FLAG_LAYOUT_IN_SCREEN 等处
// 在新版 SDK 中废弃，替代项（TYPE_APPLICATION_OVERLAY 等）要求 API 26+。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.overlay

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.text.Spanned
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView

/**
 * 屏幕顶部的一行浮层：透明、不可触摸、不可聚焦的 TextView。
 *
 * 屏幕提示与楼层状态行是两个**独立的窗口**：各自有自己的显隐判定、位置与样式，
 * 可以单独出现，也可以同时出现。本类只负责窗口本身——加窗、更新、移除与几何，
 * 不决定"什么时候该显示"。
 *
 * **窗口类型必须是 [WindowManager.LayoutParams.TYPE_SYSTEM_ERROR]，不能用 TYPE_PHONE。**
 * KitKat 的层级（PhoneWindowManager.windowTypeToLayerLw）里 TYPE_PHONE 是第 3 层，
 * 在状态栏（16）、下拉通知面板（15）、虚拟按键（20）之下——这台 ROM 的边缘划出
 * 手势是死的，系统栏一旦出现就永远挂着，文案浮在顶部会被它盖住。
 * TYPE_SYSTEM_ERROR 在第 22 层，高于上述全部系统窗口；权限仍是已有的
 * SYSTEM_ALERT_WINDOW，不新增。文案位置在顶部、不可触摸，盖过系统栏没有副作用。
 *
 * 所有方法只在主线程调用；状态 `added` 只在主线程读写。
 */
class OverlayWindow(private val context: Context) {

    /**
     * 一次显示所需的全部外观参数。
     *
     * 水平位置走 padding，不手算屏幕宽度：本 ROM 在窗口添加时机给到的 displayMetrics
     * 可能是暂态值（实测 boot 前后报过 730 而非 1366），手算的宽度上限一旦冻结就再也
     * 纠正不回来，文案在半屏处被打省略号（2026-10-24 实测）。窗口宽 MATCH_PARENT，
     * 可用宽度由框架在布局时按真实屏幕解析。
     *
     * 三种对齐共用全宽窗口：左/右用对应一侧的边距，居中用对称 padding 表达偏移
     * （内容区中心 = (paddingLeft + width - paddingRight) / 2，左 padding 加 2 * offset
     * 即中心右移 offset，负偏移则加右 padding）。
     */
    data class Style(
        val text: CharSequence,
        val textSizeSp: Int,
        val textColor: Int,
        val align: String,
        val centerOffsetDp: Int,
        val xDp: Int,
        val yDp: Int,
        val backgroundColor: Int = Color.TRANSPARENT,
        val horizontalPaddingDp: Int = 0,
        val verticalPaddingDp: Int = 0,
    )

    private val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager

    private val textView = TextView(context).apply {
        // 单行展示用 maxLines 而不是 setSingleLine(true)：后者会把 mHorizontallyScrolling
        // 置为 true，TextView.onMeasure 随即把布局宽度算成 VERY_WIDE（约 100 万像素）。
        // 此时 CENTER_HORIZONTAL 会把文字居中到这块极宽画布的中间，窗口里什么都看不到
        // （API 19 实测，2026-10-25）。maxLines=1 的布局宽度等于窗口宽度，居中才有意义。
        maxLines = 1
        ellipsize = TextUtils.TruncateAt.END
        includeFontPadding = false
        gravity = Gravity.START or Gravity.TOP
        setPadding(0, 0, 0, 0)
        importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO
        isFocusable = false
        isClickable = false
    }

    private var added = false

    val isAdded: Boolean get() = added

    /** 添加窗口并应用文字/样式/位置；返回是否已附着。失败时由控制器排一次性重试。 */
    fun show(style: Style): Boolean {
        if (added) return update(style)
        apply(style)
        return try {
            wm.addView(textView, layoutParams(style))
            added = true
            true
        } catch (e: Exception) {
            added = textView.windowToken != null
            Log.w(TAG, "addView failed: ${e.message}")
            added
        }
    }

    /** 原位更新文字/样式/位置；窗口已被系统移除时返回 false 并把 added 置回 false。 */
    fun update(style: Style): Boolean {
        if (!added) return false
        apply(style)
        return try {
            wm.updateViewLayout(textView, layoutParams(style))
            true
        } catch (e: Exception) {
            added = textView.windowToken != null
            Log.w(TAG, "updateViewLayout failed: ${e.message}")
            false
        }
    }

    /** 移除窗口；仍附着时返回 false，控制器据此保持隐藏判断。 */
    fun hide(): Boolean {
        if (!added) return true
        return try {
            wm.removeView(textView)
            added = false
            true
        } catch (e: Exception) {
            val detached = textView.windowToken == null
            added = !detached
            Log.w(TAG, "removeView failed: ${e.message}")
            detached
        }
    }

    /** 服务销毁时尽力移除；之后不再使用本实例。 */
    fun destroy() {
        if (!added) return
        try {
            wm.removeView(textView)
        } catch (e: Exception) {
            Log.w(TAG, "removeView on destroy failed: ${e.message}")
        }
        added = textView.windowToken != null
    }

    private fun apply(style: Style) {
        // 先定字号与基色，再放文字。带 ForegroundColorSpan 的文字走 SPANNABLE：
        // 实测本 ROM 下经 SpannedString 包装的 ForegroundColorSpan 不生效（0.5.5
        // 状态行整行仍是白色）；直接交给 TextView 持有 Spannable 才画出分段颜色。
        textView.setTextSize(TypedValue.COMPLEX_UNIT_SP, style.textSizeSp.coerceAtLeast(1).toFloat())
        textView.setTextColor(style.textColor)
        if (style.text is Spanned) {
            textView.setText(style.text, TextView.BufferType.SPANNABLE)
        } else {
            textView.text = style.text
        }
        textView.setBackgroundColor(style.backgroundColor)

        val density = context.resources.displayMetrics.density
        val xPx = (style.xDp.coerceAtLeast(0) * density).toInt()
        val horizontalPad = (style.horizontalPaddingDp.coerceAtLeast(0) * density).toInt()
        val verticalPad = (style.verticalPaddingDp.coerceAtLeast(0) * density).toInt()
        when (style.align) {
            OverlayAlign.CENTER -> {
                textView.gravity = Gravity.CENTER_HORIZONTAL or Gravity.TOP
                val shift = (style.centerOffsetDp * density).toInt() * 2
                textView.setPadding(
                    shift.coerceAtLeast(0) + horizontalPad,
                    verticalPad,
                    (-shift).coerceAtLeast(0) + horizontalPad,
                    verticalPad,
                )
            }

            OverlayAlign.RIGHT -> {
                textView.gravity = Gravity.END or Gravity.TOP
                textView.setPadding(horizontalPad, verticalPad, xPx + horizontalPad, verticalPad)
            }

            else -> {
                textView.gravity = Gravity.START or Gravity.TOP
                textView.setPadding(xPx + horizontalPad, verticalPad, horizontalPad, verticalPad)
            }
        }
        textView.requestLayout()
    }

    private fun layoutParams(style: Style): WindowManager.LayoutParams {
        val density = context.resources.displayMetrics.density
        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_SYSTEM_ERROR,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            // 水平偏移在 view 的 padding 里（见 [Style]）；窗口只管垂直定位
            y = (style.yDp.coerceAtLeast(0) * density).toInt()
        }
    }

    companion object {
        private const val TAG = "OverlayWindow"
    }
}
