// 设备 ROM 冻结在 Android 4.4（API 19）：TYPE_PHONE / FLAG_FULLSCREEN 等处在新版 SDK
// 中废弃，替代项（TYPE_APPLICATION_OVERLAY 等）要求 API 26+。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import java.util.concurrent.CountDownLatch

/**
 * 自动化遮盖层：分配/结算操作期间覆盖屏幕，防止人为误触。
 * 窗口类型 TYPE_PHONE + SYSTEM_ALERT_WINDOW（API 19 验证过的组合）。
 * WindowManager.addView 内部会创建 Handler，必须由带 Looper 的线程调用；
 * 流程在后台线程运行，故经主线程 Handler 投递并用 CountDownLatch 同步。
 */
class CoverOverlay(context: Context) {

    private val mainHandler = Handler(Looper.getMainLooper())
    private val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val view: LinearLayout = LinearLayout(context).apply {
        orientation = LinearLayout.VERTICAL
        gravity = Gravity.CENTER
        setBackgroundColor(Color.BLACK)
    }
    private var showing = false

    fun show(message: String) {
        if (showing) return
        runOnMain { addInternal(message) }
    }

    fun hide() {
        runOnMain { removeInternal() }
    }

    private fun runOnMain(op: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            op()
            return
        }
        val latch = CountDownLatch(1)
        mainHandler.post {
            try {
                op()
            } finally {
                latch.countDown()
            }
        }
        try {
            latch.await()
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun addInternal(message: String) {
        if (showing) return
        view.removeAllViews()
        view.addView(
            TextView(view.context).apply {
                text = message
                setTextColor(Color.WHITE)
                textSize = 22f
            },
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.MATCH_PARENT,
            ),
        )
        wm.addView(
            view,
            WindowManager.LayoutParams(
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
            },
        )
        showing = true
    }

    private fun removeInternal() {
        if (!showing) return
        try {
            wm.removeView(view)
        } catch (_: Exception) {}
        showing = false
    }
}
