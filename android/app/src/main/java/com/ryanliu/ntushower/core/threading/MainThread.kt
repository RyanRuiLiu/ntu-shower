package com.ryanliu.ntushower.core.threading

import android.os.Handler
import android.os.Looper
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * 主线程投递工具。
 *
 * 设备端多处需要在主线程执行 View / WindowManager 操作（它们要求调用线程带 Looper），
 * 而自动化流程跑在后台线程。这段"投递 + 同步等待"的逻辑此前在两处各写一遍，
 * 且超时行为不一致——此处收敛为单一实现。
 */
object MainThread {

    /**
     * 主线程执行结果的等待上限。
     *
     * 必须设上限：主线程被长任务占用时，无超时的等待会让调用方线程永久卡死。
     */
    const val AWAIT_TIMEOUT_MS = 3_000L

    private val handler = Handler(Looper.getMainLooper())

    /** 当前线程是否就是主线程。 */
    fun isCurrent(): Boolean = Looper.myLooper() == Looper.getMainLooper()

    /** 投递到主线程执行，不等待结果（用于通知/清理类操作）。 */
    fun post(op: () -> Unit) {
        if (isCurrent()) {
            op()
            return
        }
        handler.post(op)
    }

    /**
     * 投递到主线程执行并等待完成。
     *
     * @return 是否在超时前完成；false 表示主线程繁忙，操作结果未知
     */
    fun runAndWait(op: () -> Unit): Boolean {
        if (isCurrent()) {
            op()
            return true
        }
        val latch = CountDownLatch(1)
        handler.post {
            try {
                op()
            } finally {
                latch.countDown()
            }
        }
        return try {
            latch.await(AWAIT_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            false
        }
    }
}
