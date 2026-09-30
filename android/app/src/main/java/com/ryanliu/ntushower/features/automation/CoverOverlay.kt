// 设备 ROM 冻结在 Android 4.4（API 19）：TYPE_PHONE / FLAG_FULLSCREEN 等处在新版 SDK
// 中废弃，替代项（TYPE_APPLICATION_OVERLAY 等）要求 API 26+。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import com.ryanliu.ntushower.R
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.threading.MainThread

/**
 * 自动化遮盖层：分配/结算期间盖住整屏，防止现场学生误触。
 *
 * **背景用浴室 APP 自己的「网络异常」提示图**（`fm_hint.png`），默认文案是
 * 「网络连接中，请稍后重试...」——两样都取自旧项目，不是随手挑的。现场学生看到网络
 * 问题会安静等一会儿，看到「系统正在远程操作」则可能去拔电源或找来管理员。
 * 遮盖要挡的不只是手指，还得让这段等待显得合理。
 *
 * 文案与样式全部经 [RuntimeConfig] 下发，所以改一句话不必重新发版。
 *
 * 窗口类型 TYPE_PHONE + SYSTEM_ALERT_WINDOW（API 19 上验证过的组合）。
 * `WindowManager.addView` 内部会创建 Handler，必须由带 Looper 的线程调用；流程跑在
 * 后台线程，故经主线程 Handler 投递并用 CountDownLatch 同步。
 */
class CoverOverlay(context: Context) {

    private val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val view: LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER_VERTICAL
        setBackgroundResource(R.drawable.fm_hint)
    }
    private var showing = false

    /**
     * 盖住屏幕。文案与样式取自 [RuntimeConfig]，改配置后下一次 show 生效。重复调用是空操作。
     *
     * 主线程繁忙时 [MainThread.runAndWait] 会超时放弃，那时遮盖其实没盖上。**不因此中断流程**
     * ——遮盖是给现场学生看的，自动化本身不依赖它；但必须留痕，否则「学生点到了界面」会被
     * 当成灵异事件查。
     */
    fun show() {
        if (showing) return
        if (!MainThread.runAndWait { addInternal() }) {
            Log.w(TAG, "overlay show timed out, screen left uncovered")
        }
    }

    /** 撤掉遮盖层。没在显示时是空操作。 */
    fun hide() {
        if (!MainThread.runAndWait { removeInternal() }) {
            Log.w(TAG, "overlay hide timed out")
        }
    }

    private fun addInternal() {
        if (showing) return
        view.removeAllViews()
        // 文案为空时不放 TextView：设备拿到下发的配置之前 RuntimeConfig 里就是空的，
        // 那时只显示背景图，比在图上压一行空白更像话
        if (RuntimeConfig.overlayMessage.isNotEmpty()) {
            view.addView(buildMessage(RuntimeConfig.overlayMessage))
        }
        wm.addView(view, coverLayoutParams())
        showing = true
    }

    /**
     * 遮罩上的那行字。
     *
     * 左边距把文字推到提示图右侧的空白区，而不是压在图上——那是旧项目算好的位置，
     * 换一个数就盖住图标。
     */
    private fun buildMessage(message: String): TextView {
        val density = view.resources.displayMetrics.density
        val textView = TextView(view.context).apply {
            text = message
            setTextSize(TypedValue.COMPLEX_UNIT_SP, RuntimeConfig.overlayTextSizeSp.toFloat())
            setTextColor(parseColorSafely(RuntimeConfig.overlayTextColor))
            gravity = Gravity.CENTER_VERTICAL
        }
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.MATCH_PARENT,
        )
        params.leftMargin = (RuntimeConfig.overlayMarginLeftDp * density).toInt()
        textView.layoutParams = params
        return textView
    }

    /**
     * 解析十六进制颜色，格式不对时退回白色。
     *
     * 颜色是服务端下发的字符串，写错一个字 `Color.parseColor` 就会抛异常——而它发生在
     * 主线程创建窗口的过程中，异常会让整次自动化连同遮盖一起失败。一个配色笔误不该
     * 有这个后果。
     */
    private fun parseColorSafely(value: String): Int = try {
        Color.parseColor(value)
    } catch (_: IllegalArgumentException) {
        Color.WHITE
    }

    private fun coverLayoutParams(): WindowManager.LayoutParams = WindowManager.LayoutParams(
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.MATCH_PARENT,
        WindowManager.LayoutParams.TYPE_PHONE,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_FULLSCREEN,
        PixelFormat.OPAQUE,
    ).apply {
        gravity = Gravity.TOP or Gravity.START
        x = 0
        y = 0
    }

    private fun removeInternal() {
        if (!showing) return
        try {
            wm.removeView(view)
        } catch (_: Exception) {
            // 视图可能已被系统回收（服务重启/窗口管理器异常）：撤层失败不影响主流程，
            // showing 标志照常复位，下次 show 会重新添加
        }
        showing = false
    }

    companion object {
        private const val TAG = "CoverOverlay"
    }
}
