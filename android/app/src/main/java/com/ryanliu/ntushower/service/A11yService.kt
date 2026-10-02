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
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.a11y.A11yNodeSource
import com.ryanliu.ntushower.core.a11y.A11yRuntime
import com.ryanliu.ntushower.core.clock.Uptime
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.automation.BathAppGuard
import com.ryanliu.ntushower.features.automation.BathAppPage
import com.ryanliu.ntushower.features.automation.ContentEvent

/**
 * 无障碍服务实体：UI 操作原语与页面事件流。
 *
 * - 点击：ACTION_CLICK（API 19 可用的点击方式）
 * - 输入：剪贴板 + ACTION_PASTE（API 18+；ACTION_SET_TEXT 需 API 21 不可用）
 * - 页面变化：事件原样交给 [BathAppPage]，由它筛掉与页面判定无关的那些再重算
 * - 浴室 App 退出守护：阈值与冷却时长都是配置项，决策在 [BathAppGuard]
 *
 * 绑定状态、前台标志与页面变化信令不在本类：它们的使用者横跨 features 与 service，
 * 存放在 [A11yRuntime]（拆分理由见其类注释）。本服务是写入方——绑定时注册、窗口事件
 * 维护前台标志、销毁时解绑；features 侧经 [A11yNodeSource] 只读与等待。
 */
// TooManyFunctions：生命周期两条路径（绑定自举、事件分流）与 A11yNodeSource 的原语实现在
// 同一个类里是有意的——原语操作的就是本服务持有的无障碍会话，拆开反而要互相暴露状态。
@Suppress("TooManyFunctions")
class A11yService :
    AccessibilityService(),
    A11yNodeSource {

    override fun onServiceConnected() {
        super.onServiceConnected()
        A11yRuntime.bind(this)
        Log.i(TAG, "connected")
        enableViewIdReporting()
        A11yRuntime.bathAppInForeground = isBathAppInForeground()
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
            // 重算线程算完会经 A11yRuntime.onPageRefreshed 放行等待者。
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
                A11yRuntime.bathAppInForeground = true
                // 回到前台即清除退出历史与冷却：不论此前是否处于冷却期，
                // App 能回到前台就说明它自己活过来了。此前只在冷却期清除，
                // 会让未达阈值的退出记录残留，导致下次退出提前触发冷却。
                guard.onForeground()
            } else if (A11yRuntime.bathAppInForeground) {
                A11yRuntime.bathAppInForeground = false
                handleBathAppExit(pkg)
            }
        }
    }

    /** 浴室 App 退出到桌面：崩溃守护决策（拉起 / 冷却），自动化期间不拉起。 */
    private fun handleBathAppExit(leftToPackage: String) {
        if (!RuntimeConfig.bathGuardEnabled) return
        val now = Uptime.nowMs()
        // 阈值每次从 RuntimeConfig 现取：远程配置改动即时生效，
        // 不必等服务因故重绑重建这个守护（构造期固化是曾经的缺陷）
        val params = BathAppGuard.Params(
            windowMs = RuntimeConfig.bathExitWindowMs,
            maxCount = RuntimeConfig.bathExitMaxCount,
            cooldownMs = RuntimeConfig.bathExitCooldownMs,
        )
        when (guard.onBackground(now, params)) {
            BathAppGuard.Action.RELAUNCH ->
                if (!A11yRuntime.automationInProgress) {
                    Log.i(TAG, "bath app exited to $leftToPackage, relaunching")
                    launchBathApp()
                }

            BathAppGuard.Action.COOLDOWN ->
                Log.w(TAG, "bath app exited too often, cooldown ${RuntimeConfig.bathExitCooldownMs}ms")

            BathAppGuard.Action.NONE -> Unit
        }
    }

    private val guard = BathAppGuard()

    override fun onInterrupt() {
        // 系统中断无障碍服务：系统会自行重连，无恢复动作
    }

    override fun onDestroy() {
        A11yRuntime.unbind()
        // 自动化完全依赖本服务，服务销毁时它不可能还在跑。不复位的话这个标志会停在 true，
        // 而 BathAppGuard 据此不再拉起浴室 App——那正是界面能力的自举锚点，
        // 一次服务重绑就会让设备从此不再自愈。
        A11yRuntime.automationInProgress = false
        super.onDestroy()
    }

    // ── UI 操作原语（A11yNodeSource） ──

    /** 活动窗口的根节点；调用方负责 recycle（API 19 上不回收会泄漏节点）。 */
    override fun activeWindowRoot(): AccessibilityNodeInfo? = super.getRootInActiveWindow()

    override fun performClick(node: AccessibilityNodeInfo): Boolean =
        node.performAction(AccessibilityNodeInfo.ACTION_CLICK)

    /**
     * 向节点输入文本：主线程写剪贴板 → 聚焦 → ACTION_PASTE。
     * 4.4 上 ClipboardManager 构造需主线程 Looper，故经 MainThread 投递并同步等待。
     */
    override fun performSetText(node: AccessibilityNodeInfo, text: String): Boolean {
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
    override fun clearClipboard() {
        MainThread.post {
            try {
                (getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager)
                    ?.setPrimaryClip(ClipData.newPlainText("", ""))
            } catch (_: Exception) {
                // 清理失败无后续依赖：下次输入会重新写入剪贴板
            }
        }
    }

    override fun sendBroadcast(intent: Intent) {
        super.sendBroadcast(intent)
    }

    fun isBathAppInForeground(): Boolean {
        val root = activeWindowRoot() ?: return false
        try {
            return root.packageName?.toString() == AppConfig.BATH_APP_PACKAGE
        } finally {
            root.recycle()
        }
    }

    /** 热切换浴室 App 到前台（REORDER_TO_FRONT 不触发生命周期重建）。 */
    override fun launchBathApp(): Boolean = try {
        val intent = packageManager.getLaunchIntentForPackage(AppConfig.BATH_APP_PACKAGE) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        startActivity(intent)
        true
    } catch (e: Exception) {
        Log.e(TAG, "launch bath app failed: ${e.message}")
        false
    }

    /**
     * 屏幕旋转角度（0/1/2/3，`Display.getRotation()` 语义），供节点树导出取用。
     *
     * 不能用 `Configuration.orientation` 顶替：那个只有竖屏(1)/横屏(2) 两值，
     * 在旋转 180° 与 270° 时都会给出同一个答案，按它换算坐标必然错位。
     */
    override fun screenRotation(): Int = try {
        (getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.rotation
    } catch (_: Exception) {
        0
    }

    companion object {
        private const val TAG = "A11yService"

        /** 剪贴板条目标签。 */
        private const val CLIPBOARD_LABEL = "ntushower"
    }
}
