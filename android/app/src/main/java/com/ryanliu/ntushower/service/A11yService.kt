package com.ryanliu.ntushower.service

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.features.automation.BathAppGuard
import java.util.concurrent.CountDownLatch

/**
 * 无障碍服务实体：UI 操作原语与页面事件流。
 *
 * - 点击：ACTION_CLICK（API 19 兼容）
 * - 输入：剪贴板 + ACTION_PASTE（API 18+；ACTION_SET_TEXT 需 API 21 不可用）
 * - 页面等待：TYPE_WINDOW_CONTENT_CHANGED 事件唤醒，替代固定轮询
 * - 浴室 App 崩溃守护：退出到桌面 30s 内 ≥3 次进入 10 分钟冷却期，冷却期内不自动拉起
 */
class A11yService : AccessibilityService() {

    private val mainHandler = Handler(Looper.getMainLooper())

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "connected")
        bathAppInForeground = isBathAppInForeground()
        // 自举主服务：无障碍绑定是本设备上唯一可靠的开机拉起锚点——
        // DS83X ROM 不投递 BOOT_COMPLETED（真机实测 + 旧项目同结论），
        // 系统开机后主动重绑已启用的无障碍服务（不受 stopped 状态影响）。
        // 设置变更/崩溃后重绑同样走到这里；旧系统真机验证过的模式。
        startService(Intent(this, AgentService::class.java))
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            synchronized(pageChangeLock) { pageChangeLock.notifyAll() }
        }
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val pkg = event.packageName?.toString() ?: ""
            if (pkg == AppConfig.BATH_APP_PACKAGE) {
                bathAppInForeground = true
                if (guard.isCoolingDown(System.currentTimeMillis())) {
                    guard.onForeground()
                }
            } else if (bathAppInForeground) {
                bathAppInForeground = false
                handleBathAppExit(pkg)
            }
        }
    }

    /** 浴室 App 退出到桌面：崩溃守护决策（拉起 / 冷却），自动化期间不拉起。 */
    private fun handleBathAppExit(leftToPackage: String) {
        if (!RuntimeConfig.bathGuardEnabled) return
        val now = System.currentTimeMillis()
        when (guard.onBackground(now)) {
            BathAppGuard.Action.RELAUNCH ->
                if (!automationInProgress) {
                    Log.i(TAG, "bath app exited to $leftToPackage, relaunching")
                    launchBathApp()
                }

            BathAppGuard.Action.COOLDOWN ->
                Log.w(TAG, "bath app exited too often, cooldown ${RuntimeConfig.bathExitCooldownMs}ms")

            BathAppGuard.Action.NONE -> Unit
        }
    }

    private val guard = BathAppGuard(
        windowMs = RuntimeConfig.bathExitWindowMs,
        maxCount = RuntimeConfig.bathExitMaxCount,
        cooldownMs = RuntimeConfig.bathExitCooldownMs,
    )

    override fun onInterrupt() {
        // 系统中断无障碍服务：系统会自行重连，无恢复动作
    }

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }

    // ── UI 操作原语 ──

    fun performClick(node: AccessibilityNodeInfo): Boolean = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)

    /**
     * 向节点输入文本：主线程写剪贴板 → 聚焦 → ACTION_PASTE。
     * 4.4 上 ClipboardManager 构造需主线程 Looper，故经 Handler.post + CountDownLatch 同步。
     */
    fun performSetText(node: AccessibilityNodeInfo, text: String): Boolean {
        val latch = CountDownLatch(1)
        var clipboardOk = false
        mainHandler.post {
            try {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                if (clipboard != null) {
                    clipboard.setPrimaryClip(ClipData.newPlainText("ntushower", text))
                    clipboardOk = true
                }
            } catch (e: Exception) {
                Log.e(TAG, "set clipboard failed: ${e.message}")
            }
            latch.countDown()
        }
        try {
            latch.await()
        } catch (e: InterruptedException) {
            return false
        }
        if (!clipboardOk) return false
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        return node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    /** 输入完成后清空剪贴板，防止账密残留（异步）。 */
    fun clearClipboard() {
        mainHandler.post {
            try {
                (getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager)
                    ?.setPrimaryClip(ClipData.newPlainText("", ""))
            } catch (_: Exception) {}
        }
    }

    fun isBathAppInForeground(): Boolean {
        val root = rootInActiveWindow ?: return false
        try {
            return root.packageName?.toString() == AppConfig.BATH_APP_PACKAGE
        } finally {
            root.recycle()
        }
    }

    /** 热切换浴室 App 到前台（REORDER_TO_FRONT 不触发生命周期重建）。 */
    fun launchBathApp(): Boolean = try {
        val intent = packageManager.getLaunchIntentForPackage(AppConfig.BATH_APP_PACKAGE) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        startActivity(intent)
        true
    } catch (e: Exception) {
        Log.e(TAG, "launch bath app failed: ${e.message}")
        false
    }

    companion object {
        private const val TAG = "A11yService"

        @Volatile
        var instance: A11yService? = null
            private set

        private val pageChangeLock = Object()

        /** 阻塞等待页面内容变化或超时（事件驱动，无人等待时零开销）。 */
        @JvmStatic
        fun awaitPageChange(timeoutMs: Long) {
            synchronized(pageChangeLock) { pageChangeLock.wait(timeoutMs) }
        }

        @Volatile
        var bathAppInForeground = false
            private set

        /** 自动化流程执行中标志（崩溃守护在此期间不拉起）。 */
        @Volatile
        var automationInProgress = false
    }
}
