// 设备 ROM 冻结在 Android 4.4（API 19）：TYPE_SYSTEM_ERROR / FLAG_LAYOUT_IN_SCREEN 等处
// 在新版 SDK 中废弃，替代项（TYPE_APPLICATION_OVERLAY 等）要求 API 26+。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.notice

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.text.TextUtils
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView

/**
 * 屏幕提示窗口：一层透明、不可触摸、不可聚焦的 TextView，盖在浴室 App 之上。
 *
 * 只负责窗口本身；显示/隐藏由 [NoticeController] 依据 [NoticeRule] 的结果驱动。
 *
 * **窗口类型必须是 [WindowManager.LayoutParams.TYPE_SYSTEM_ERROR]，不能用 TYPE_PHONE。**
 * KitKat 的层级（PhoneWindowManager.windowTypeToLayerLw）里 TYPE_PHONE 是第 3 层，
 * 在状态栏（16）、下拉通知面板（15）、虚拟按键（20）之下——这台 ROM 的边缘划出
 * 手势是死的，系统栏一旦出现就永远挂着，提示文案浮在顶部会被它盖住。
 * TYPE_SYSTEM_ERROR 在第 22 层，高于上述全部系统窗口；权限仍是已有的
 * SYSTEM_ALERT_WINDOW，不新增。文案位置在顶部、不可触摸，盖过系统栏没有副作用。
 *
 * 所有方法只在主线程调用；状态 `added` 只在主线程读写。
 */
class NoticeOverlay(private val context: Context) {

    private val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager

    private val textView = TextView(context).apply {
        setSingleLine(true)
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
    fun show(settings: NoticeSettings): Boolean {
        if (added) return update(settings)
        applyText(settings)
        return try {
            wm.addView(textView, layoutParams(settings))
            added = true
            true
        } catch (e: Exception) {
            added = textView.windowToken != null
            Log.w(TAG, "addView failed: ${e.message}")
            added
        }
    }

    /** 原位更新文字/样式/位置；窗口已被系统移除时返回 false 并把 added 置回 false。 */
    fun update(settings: NoticeSettings): Boolean {
        if (!added) return false
        applyText(settings)
        return try {
            wm.updateViewLayout(textView, layoutParams(settings))
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

    private fun applyText(settings: NoticeSettings) {
        val text = settings.text.replace('\r', ' ').replace('\n', ' ').trim()
        textView.text = text
        textView.setTextSize(TypedValue.COMPLEX_UNIT_SP, settings.textSizeSp.coerceAtLeast(1).toFloat())
        textView.setTextColor(parseColorSafely(settings.textColor))
        // 水平偏移走 padding 而不是「maxWidth = widthPixels - x」：本 ROM 在窗口添加时机
        // 给到的 displayMetrics 可能是暂态值（实测 boot 前后报过 730 而非 1366），手算的
        // 宽度上限一旦冻结就再也纠正不回来，文案在半屏处被打省略号（2026-10-24 实测）。
        // 窗口宽 MATCH_PARENT（见 layoutParams），可用宽度由框架在布局时按真实屏幕解析。
        val xPx = (settings.xDp.coerceAtLeast(0) * context.resources.displayMetrics.density).toInt()
        textView.setPadding(xPx, 0, 0, 0)
        textView.requestLayout()
    }

    private fun layoutParams(settings: NoticeSettings): WindowManager.LayoutParams {
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
            // 水平偏移在 view 的 padding 里（见 applyText）；窗口只管垂直定位
            y = (settings.yDp.coerceAtLeast(0) * density).toInt()
        }
    }

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
