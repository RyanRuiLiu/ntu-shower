// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.automation.BathAppGuard
import com.ryanliu.ntushower.features.automation.BathAppPage
import com.ryanliu.ntushower.features.automation.ContentEvent
import java.util.concurrent.atomic.AtomicLong

/**
 * 无障碍服务实体：UI 操作原语与页面事件流。
 *
 * - 点击：ACTION_CLICK（API 19 可用的点击方式）
 * - 输入：剪贴板 + ACTION_PASTE（API 18+；ACTION_SET_TEXT 需 API 21 不可用）
 * - 页面变化：事件原样交给 [BathAppPage]，由它筛掉与页面判定无关的那些再重算
 * - 浴室 App 退出守护：阈值与冷却时长都是配置项，决策在 [BathAppGuard]
 */
class A11yService : AccessibilityService() {

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        Log.i(TAG, "connected")
        enableViewIdReporting()
        bathAppInForeground = isBathAppInForeground()
        // 自举主服务：无障碍绑定是本设备上唯一可靠的开机拉起锚点——
        // DS83X ROM 不投递 BOOT_COMPLETED（真机实测 + 旧项目同结论），
        // 系统开机后主动重绑已启用的无障碍服务（不受 stopped 状态影响）。
        // 设置变更/崩溃后重绑同样走到这里；旧系统真机验证过的模式。
        startService(Intent(this, AgentService::class.java))
    }

    /**
     * 开启控件 ID 上报。
     *
     * API 18 起 `AccessibilityNodeInfo.viewIdResourceName` 默认不填——不设这个标志
     * 它恒为 null。控件的资源 ID 是定位元素最稳的依据（文本会因语言与状态变化，
     * 坐标会因布局变化），缺了它远端的节点树只能靠文本匹配。
     */
    private fun enableViewIdReporting() {
        val info = serviceInfo ?: return
        info.flags = info.flags or AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
        serviceInfo = info
        Log.i(TAG, "view id reporting enabled")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            // 只入队，不在这里算。重算要做同步 IPC，而本回调跑在**主线程**上——真机上正是
            // 这条路径把主线程按住，让看门狗的广播排不上队、超时后进程被系统以 ANR 杀掉。
            // 重算线程算完会经 onPageRefreshed 放行等待者。
            //
            // 附带的事件特征只用本地字段（见 ContentEvent）。getSource() 刻意不取：它为拿一个
            // 节点要发起一次跨进程调用，而这类事件每秒来五十来条。
            BathAppPage.refresh(
                ContentEvent(
                    className = event.className?.toString() ?: "",
                    contentChangeTypes = event.contentChangeTypes,
                ),
            )
        }
        if (event.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            val pkg = event.packageName?.toString() ?: ""
            if (pkg == AppConfig.BATH_APP_PACKAGE) {
                bathAppInForeground = true
                // 回到前台即清除退出历史与冷却：不论此前是否处于冷却期，
                // App 能回到前台就说明它自己活过来了。此前只在冷却期清除，
                // 会让未达阈值的退出记录残留，导致下次退出提前触发冷却。
                guard.onForeground()
            } else if (bathAppInForeground) {
                bathAppInForeground = false
                handleBathAppExit(pkg)
            }
        }
    }

    /** 浴室 App 退出到桌面：崩溃守护决策（拉起 / 冷却），自动化期间不拉起。 */
    private fun handleBathAppExit(leftToPackage: String) {
        if (!RuntimeConfig.bathGuardEnabled) return
        val now = Uptime.nowMs()
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
        // 自动化完全依赖本服务，服务销毁时它不可能还在跑。不复位的话这个标志会停在 true，
        // 而 BathAppGuard 据此不再拉起浴室 App——那正是界面能力的自举锚点，
        // 一次服务重绑就会让设备从此不再自愈。
        automationInProgress = false
        super.onDestroy()
    }

    // ── UI 操作原语 ──

    fun performClick(node: AccessibilityNodeInfo): Boolean = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)

    /**
     * 向节点输入文本：主线程写剪贴板 → 聚焦 → ACTION_PASTE。
     * 4.4 上 ClipboardManager 构造需主线程 Looper，故经 MainThread 投递并同步等待。
     */
    fun performSetText(node: AccessibilityNodeInfo, text: String): Boolean {
        var clipboardOk = false
        // 主线程若被长时间占用，这里必须能放弃：无限等待会让调用方线程永久卡死
        val done = MainThread.runAndWait {
            try {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                if (clipboard != null) {
                    clipboard.setPrimaryClip(ClipData.newPlainText(CLIPBOARD_LABEL, text))
                    clipboardOk = true
                }
            } catch (e: Exception) {
                Log.e(TAG, "set clipboard failed: ${e.message}")
            }
        }
        if (!done) {
            Log.w(TAG, "set clipboard timed out")
            return false
        }
        if (!clipboardOk) return false
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        return node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    /** 输入完成后清空剪贴板，防止账密残留（异步）。 */
    fun clearClipboard() {
        MainThread.post {
            try {
                (getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager)
                    ?.setPrimaryClip(ClipData.newPlainText("", ""))
            } catch (_: Exception) {
                // 清理失败无后续依赖：下次输入会重新写入剪贴板
            }
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

        /** 剪贴板条目标签。 */
        private const val CLIPBOARD_LABEL = "ntushower"

        @Volatile
        var instance: A11yService? = null
            private set

        private val pageChangeLock = Object()

        /** 已完成的重算次数：配合 [pageChangeMark] / [awaitPageChangeAfter] 用。 */
        private val pageChangeCount = AtomicLong(0)

        /**
         * 一次页面重算已完成（由 [BathAppPage] 的重算线程调用）：放行正在等页面变化的调用方。
         *
         * 由重算线程调用而不是由事件回调调用，是这次线程模型调整的关键：事件回调跑在主线程，
         * 那里不能做重算要的同步 IPC；而"放行等待者"必须发生在**状态已经跟上之后**，等待方
         * 被唤醒就会立刻读 [BathAppPage.snapshot]，反过来就是它醒来看见旧值、白转一圈再等一轮。
         *
         * 多次界面变化可能合并成一次重算，所以这个计数不再等于"内容变化次数"，而是"重算次数"
         * ——对 [awaitPageChangeAfter] 的用法（只比"是否大于动手前那个 mark"）没有影响。
         */
        @JvmStatic
        fun onPageRefreshed() {
            pageChangeCount.incrementAndGet()
            synchronized(pageChangeLock) { pageChangeLock.notifyAll() }
        }

        /**
         * 取当前的变化计数。**在动手之前取**——之后拿它判断"这一下有没有让页面变"。
         */
        @JvmStatic
        fun pageChangeMark(): Long = pageChangeCount.get()

        /**
         * 等到页面内容在 [mark] 之后发生变化，最多等 [timeoutMs]（事件驱动，无人等待时零开销）。
         *
         * 为什么要传一个 mark，而不是单纯 `wait`：`Object.wait` 等的是**下一次**通知，
         * 通知若发生在调用之前就永远等不到，只能等满上限。而"点完按钮页面立刻就变了"恰恰
         * 是最常见的情形——那一拍纯属白等，一个流程里要发生五六次。
         *
         * 拿到锁之后再比一次，是为了盖住"取 mark 到进锁之间"那一小段；`wait` 被无关通知
         * 唤醒时也会绕回循环再比。
         */
        @JvmStatic
        fun awaitPageChangeAfter(mark: Long, timeoutMs: Long) {
            synchronized(pageChangeLock) {
                val deadline = Uptime.nowMs() + timeoutMs
                while (pageChangeCount.get() == mark) {
                    val left = deadline - Uptime.nowMs()
                    if (left <= 0) return
                    pageChangeLock.wait(left)
                }
            }
        }

        /** 阻塞等待下一次页面变化或超时；不关心"此前是否已经变过"的场景用它。 */
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
