// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。本文件取到的节点都要回收。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.service

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 界面操作的无障碍通道：点击、按键、输入。
 *
 * 与 root shell 通道（[ShellUiChannel]）的关系与各自适用场景见 [UiChannel]。
 *
 * 通道内部分工：[UiTreeCodec] 负责"看"，这里负责"改"。
 * 写操作会真的动到界面，因此由 CommandScheduler 串行化。
 *
 * ## API 19 的约束
 *
 * `dispatchGesture` 需要 API 24，本设备 ROM 冻结在 4.4，**无法合成任意坐标手势**。
 * 因此：
 * - 点击坐标 → 取该点下最深且可点击的节点再 `ACTION_CLICK`（更可靠）
 * - 滑动 → 只能走 root shell 的 `input swipe`（见 CommandScheduler 的说明）
 * - 文本 → 剪贴板 + `ACTION_PASTE`（`ACTION_SET_TEXT` 需 API 21）
 */
object A11yUiChannel {

    private const val TAG = "A11yUiChannel"

    /** 剪贴板条目标签。 */
    private const val CLIPBOARD_LABEL = "ntushower"

    /**
     * 按屏幕坐标点击：取该点下最深且可点击的节点。
     *
     * 坐标不是首选——布局变化后它失效。元素定位应由选择器承担
     * （见 [UiChannel] 的说明），目前尚未实现，因此这是唯一的点击方式。
     */
    fun clickAt(service: A11yService, x: Int, y: Int): Boolean {
        val root = service.rootInActiveWindow ?: return false
        try {
            val target = deepestClickableAt(root, x, y, Rect())
            return if (target != null && target != root) {
                try {
                    target.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                } finally {
                    target.recycle()
                }
            } else {
                false
            }
        } finally {
            root.recycle()
        }
    }

    /**
     * 滚动可滚动容器。
     *
     * 用 `ACTION_SCROLL_FORWARD` / `ACTION_SCROLL_BACKWARD`（API 16+）——这是
     * API 19 上唯一可用的滚动接口，`dispatchGesture` 要 API 24。
     *
     * 目标容器取**深度优先遇到的第一个可见的可滚动节点**。曾经用过"屏幕中心命中
     * 的那个"（本意是"用户在看的那个"），真机上被否定：设置界面是横屏分栏，列表
     * 只占左半屏（`[0,81][546,720]`），而屏幕中心落在右半屏——明明有可滚容器却报
     * "没有"。布局位置不是判断依据。
     *
     * 多个可滚动容器同时可见时，它选中遍历到的第一个；要精确指定需要 UiSelector
     * 风格的选择器，尚未实现（见 [UiChannel] 的说明）。
     */
    fun scroll(service: A11yService, forward: Boolean): Boolean {
        val root = service.rootInActiveWindow ?: return false
        try {
            val target = firstScrollable(root) ?: return false
            val action = if (forward) {
                AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            } else {
                AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            }
            return try {
                target.performAction(action)
            } finally {
                if (target != root) target.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    /**
     * 深度优先找第一个可见的可滚动节点。调用方负责 recycle 返回值。
     *
     * 两个条件都要：`isScrollable` 是容器自身的属性；`isVisibleToUser` 排除抽屉、
     * 已关闭的浮层这类"在树里但用户看不到"的容器——对它们发滚动会静默无效。
     *
     * **命中后要先回收一路上的 child 再返回**。早先写成 `if (hit != null) return hit`，
     * 那句 `child.recycle()` 就被跳过了——沿途每一层都漏一个节点。`getChild` 返回的是
     * 独立对象，回收 child 不会让子树里的 hit 失效。
     */
    private fun firstScrollable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isScrollable && node.isVisibleToUser) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val hit = firstScrollable(child)
            child.recycle()
            if (hit != null) return hit
        }
        return null
    }

    /**
     * 在 `(x, y)` 处深度优先找最深且可点击的节点。调用方负责 recycle 返回值。
     *
     * `rect` 由调用方传入并逐层复用：递归深度可达几十层，每层新分配一个 `Rect`
     * 在每次点击里都是无谓的分配。
     */
    private fun deepestClickableAt(node: AccessibilityNodeInfo, x: Int, y: Int, rect: Rect): AccessibilityNodeInfo? {
        node.getBoundsInScreen(rect)
        if (!rect.contains(x, y)) return null
        for (i in node.childCount - 1 downTo 0) {
            val child = node.getChild(i) ?: continue
            val hit = deepestClickableAt(child, x, y, rect)
            child.recycle()
            if (hit != null) return hit
        }
        return if (node.isClickable || node.isFocusable) node else null
    }

    /** 向当前焦点控件输入文本；无焦点时取第一个可编辑节点。 */
    fun typeIntoFocus(service: A11yService, text: String): Boolean {
        val root = service.rootInActiveWindow ?: return false
        try {
            val target = findFocusOrEditable(root) ?: return false
            return if (target == root) {
                false
            } else {
                try {
                    setText(service, target, text)
                } finally {
                    target.recycle()
                }
            }
        } finally {
            root.recycle()
        }
    }

    /**
     * 用剪贴板粘贴写入文本。
     *
     * 4.4 上 ClipboardManager 构造需主线程 Looper，故由调用方确保不在主线程上
     * 直接调用；这里只做同步的剪贴板写入。
     */
    private fun setText(service: A11yService, node: AccessibilityNodeInfo, text: String): Boolean {
        val clipboard = service.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager ?: return false
        return try {
            clipboard.setPrimaryClip(ClipData.newPlainText(CLIPBOARD_LABEL, text))
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            val pasted = node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
            // 输入后清空剪贴板：里面可能是账号密码
            clipboard.setPrimaryClip(ClipData.newPlainText("", ""))
            pasted
        } catch (e: Exception) {
            Log.e(TAG, "set text failed: ${e.message}")
            false
        }
    }

    private fun findFocusOrEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused && node.isEditable) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val hit = findFocusOrEditable(child)
            child.recycle()
            if (hit != null) return hit
        }
        return if (node.isEditable) node else null
    }

    /**
     * 发送系统按键。
     *
     * 用 `performGlobalAction` 而非 root shell 的 `input keyevent`：前者是
     * Android 给无障碍服务的正规接口，无需 root、不 spawn 进程，也不受部分
     * ROM 对 shell 输入命令的限制。
     *
     * 只支持全局动作，取值见 [UiKey]。
     */
    fun sendKey(service: A11yService, key: UiKey): Boolean = service.performGlobalAction(
        when (key) {
            UiKey.BACK -> AccessibilityService.GLOBAL_ACTION_BACK
            UiKey.HOME -> AccessibilityService.GLOBAL_ACTION_HOME
            UiKey.RECENTS -> AccessibilityService.GLOBAL_ACTION_RECENTS
            UiKey.NOTIFICATIONS -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
        },
    )
}
