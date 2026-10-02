package com.ryanliu.ntushower.core.a11y

import android.content.Intent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 无障碍节点操作的门面：features 层经它访问无障碍链路，不依赖承载它的
 * AccessibilityService 实体。
 *
 * 之所以收成接口：服务的**实体**（Android 组件，service.A11yService）与**界面原语**
 * （点击、输入、剪贴板）是两件事，后者才是 features 的操作面。直接依赖服务类会让
 * features 反向 import service，与「AgentService 装配 features」合成包级循环；
 * 收到 core 之后依赖恢复单向——A11yService 实现本接口，绑定时注册进 [A11yRuntime]。
 */
interface A11yNodeSource {

    /**
     * 活动窗口的根节点；调用方负责 recycle（API 19 上不回收会泄漏节点）。
     *
     * 做成函数而不是属性：平台的 `getRootInActiveWindow()` 与同签名属性在 JVM 上
     * 相撞，Kotlin 不允许用属性覆盖 Java 方法。
     */
    fun activeWindowRoot(): AccessibilityNodeInfo?

    /** 点击节点（ACTION_CLICK，API 19 上可用的点击方式）。 */
    fun performClick(node: AccessibilityNodeInfo): Boolean

    /**
     * 向节点输入文本：主线程写剪贴板 → 聚焦 → ACTION_PASTE。
     * API 19 没有 ACTION_SET_TEXT；且 4.4 的 ClipboardManager 构造需主线程 Looper，
     * 实现必须经 MainThread 投递并同步等待。
     */
    fun performSetText(node: AccessibilityNodeInfo, text: String): Boolean

    /** 输入完成后清空剪贴板，防止账密残留（异步）。 */
    fun clearClipboard()

    /** 热切换浴室 App 到前台（REORDER_TO_FRONT，不触发生命周期重建）。 */
    fun launchBathApp(): Boolean

    /** 发送广播（ensureForeground 关闭系统面板用）。 */
    fun sendBroadcast(intent: Intent)

    /** 屏幕旋转角度（0/1/2/3，`Display.getRotation()` 语义）。 */
    fun screenRotation(): Int
}
