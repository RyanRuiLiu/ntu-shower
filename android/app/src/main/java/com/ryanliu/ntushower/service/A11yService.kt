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
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.core.threading.MainThread
import com.ryanliu.ntushower.features.automation.BathAppGuard
import com.ryanliu.ntushower.features.automation.BathAppPage
import com.ryanliu.ntushower.features.automation.ContentEvent
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

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

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /**
     * 事件回调在主线程；写日志、守护决策与 root shell 都排到这条单线程上。
     * 单线程同时保证 [guard] 的调用顺序与事件到达顺序一致。
     */
    private val worker = Executors.newSingleThreadExecutor()

    /** 绑定后的首次前台判断是异步的；一旦收到窗口事件，以事件为准，避免旧结果盖回去。 */
    private val windowEventSeen = AtomicBoolean(false)

    override fun onServiceConnected() {
        super.onServiceConnected()
        A11yRuntime.bind(this)
        worker.execute { deviceLog.lifecycle("a11y.connected", "component=A11yService") }
        Log.i(TAG, "connected")
        enableViewIdReporting()
        windowEventSeen.set(false)
        worker.execute {
            if (!windowEventSeen.get()) A11yRuntime.bathAppInForeground = isBathAppInForeground()
        }
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
        val pkg = event.packageName?.toString() ?: ""
        // 本应用只有覆盖窗口，没有浴室 App 页面语义：提示窗与遮盖层的窗口/文字事件
        // 不能被当成浴室 App 退出，也不应推动 BathAppPage 重算。
        if (pkg == packageName) return
        windowEventSeen.set(true)
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
            if (pkg == AppConfig.BATH_APP_PACKAGE) {
                A11yRuntime.bathAppInForeground = true
                // 回到前台即清除退出历史与冷却：不论此前是否处于冷却期，
                // App 能回到前台就说明它自己活过来了。此前只在冷却期清除，
                // 会让未达阈值的退出记录残留，导致下次退出提前触发冷却。
                worker.execute { guard.onForeground() }
            } else if (A11yRuntime.bathAppInForeground) {
                A11yRuntime.bathAppInForeground = false
                worker.execute { handleBathAppExit(pkg) }
            }
        }
    }

    /**
     * 浴室 App 退出到 [leftToPackage]：崩溃守护决策（拉起 / 冷却），自动化期间不拉起。
     *
     * 去向交给守护判断：退到桌面才拉回来，退到别的包是现场人员走调试通道，不碰。
     */
    private fun handleBathAppExit(leftToPackage: String) {
        val now = Uptime.nowMs()
        // 阈值钉为常量（生产实测从未改动，2026-11-17 收编）；每次现取构造，
        // 不依赖服务实例生命周期（构造期固化是曾经的缺陷）
        val params = BathAppGuard.Params(
            windowMs = BATH_EXIT_WINDOW_MS,
            maxCount = BATH_EXIT_MAX_COUNT,
            cooldownMs = BATH_EXIT_COOLDOWN_MS,
        )
        when (guard.onBackground(now, leftToPackage, AppConfig.BATH_DESKTOP_PACKAGES, params)) {
            BathAppGuard.Action.RELAUNCH ->
                if (!A11yRuntime.automationInProgress) {
                    Log.i(TAG, "bath app exited to $leftToPackage, relaunching")
                    launchBathApp()
                }

            BathAppGuard.Action.COOLDOWN ->
                Log.w(TAG, "bath app exited too often, cooldown ${BATH_EXIT_COOLDOWN_MS}ms")

            BathAppGuard.Action.NONE -> Unit
        }
    }

    private val guard = BathAppGuard()

    override fun onInterrupt() {
        // 系统中断无障碍服务：系统会自行重连，无恢复动作
    }

    override fun onDestroy() {
        A11yRuntime.unbind()
        worker.execute { deviceLog.lifecycle("a11y.disconnected", "component=A11yService") }
        // 让已排队的日志写完，不再接受新任务；主线程不被文件 IO 占住
        worker.shutdown()
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
        // 主线程若被长时间占用，这里必须能够放弃：无限等待会使调用方线程永久停滞
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

    /**
     * 把浴室 App 拉到前台。
     *
     * 默认走**主界面**（快通路）：它不做网络初始化，真机 1~3 秒就位，而且重建 Activity 会
     * 把导航栏那组 `SYSTEM_UI_FLAG_*` 重设一遍。为什么不直接 `startActivity`、为什么要经
     * root shell，见 [launchBathMainActivity]。
     *
     * 免费名单有改动待生效时改走完整通路：名单只在启动时读一次，走主界面不做初始化，
     * 名单的更新就失去意义（见 [A11yRuntime.bathFullLaunchPending]）。
     */
    override fun launchBathApp(): Boolean {
        if (!A11yRuntime.bathFullLaunchPending) return launchBathMainActivity()
        // 清除放在成功之后：失败说明名单还没被读进去，标志要留着等下次拉起再试
        val launched = launchBathAppFull()
        if (launched) A11yRuntime.bathFullLaunchPending = false
        return launched
    }

    /**
     * 完整启动浴室 App：走 LAUNCHER 入口，会重新下载参数并重读免费名单。
     *
     * 代价是联网超时（见 [launchBathApp]），所以只在名单待生效时用。不走
     * [com.ryanliu.ntushower.features.automation.BathAppRestart]：那条路要 `force-stop`，
     * 会中断进行中的会话。
     */
    private fun launchBathAppFull(): Boolean = try {
        val intent = packageManager.getLaunchIntentForPackage(AppConfig.BATH_APP_PACKAGE) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivity(intent)
        true
    } catch (e: Exception) {
        Log.e(TAG, "launch bath app (full) failed: ${e.message}")
        false
    }

    /**
     * 拉起到浴室 App 的主界面。
     *
     * **必须经 root shell**：主界面在浴室 App 的 manifest 里没有 intent-filter，因此
     * `exported=false`，本进程（普通应用）`startActivity` 会被系统拒绝，真机日志是
     * `Permission Denial: starting Intent { ... cmp=com.datalook.bath/.BathMainActivity }
     * from ProcessRecord{... ntushower/u0a52} not exported from uid 10053`。LAUNCHER 入口
     * `WelcomeActivity` 因为带 intent-filter 而隐式导出，普通应用起得来，但它要联网下载
     * 参数、10 秒以上才进得去浴室页。
     *
     * shell 持有 `START_ANY_ACTIVITY`，能启动未导出的组件，所以这条路走 `am start`——与
     * [BathAppRestart] 依赖同一个 root 前提。`-f 0x10000000` 是 `NEW_TASK`：实测只有它时
     * 会复用已存在的浴室 task（task 数量不增长），不带则落点不确定。
     */
    private fun launchBathMainActivity(): Boolean {
        val component = "${AppConfig.BATH_APP_PACKAGE}/${AppConfig.BATH_MAIN_ACTIVITY}"
        val result = RootShell.control.exec("am start -f 0x10000000 -n $component")
        if (result.exitCode == 0) return true
        Log.e(TAG, "launch bath main activity failed: ${result.output}")
        return false
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
        private const val BATH_EXIT_WINDOW_MS = 30_000L
        private const val BATH_EXIT_MAX_COUNT = 3
        private const val BATH_EXIT_COOLDOWN_MS = 600_000L

        private const val TAG = "A11yService"

        /** 剪贴板条目标签。 */
        private const val CLIPBOARD_LABEL = "ntushower"
    }
}
