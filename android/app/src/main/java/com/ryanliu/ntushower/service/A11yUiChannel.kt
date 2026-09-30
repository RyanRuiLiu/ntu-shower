// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。本文件取到的节点都要回收。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.service

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo

/**
 * 无障碍通道里**唯一**保留的写操作：往输入框写文本。
 *
 * ## 为什么只剩它
 *
 * 网页端界面检视的其余操作（点击、滑动、滚动、按键）都走 root shell 的 `input`
 * 命令（见 [ShellUiChannel]）。它们是坐标语义，而远程操作的人本来就是指着截图上的
 * 某个位置点下去的——坐标天然对应，不需要经过节点树兜一圈。
 *
 * **输入是唯一的例外，因为它必须支持中文**。shell 的 `input text` 在本 ROM 上按
 * 字节处理，非 ASCII 会被写坏；而剪贴板 + `ACTION_PASTE` 是 API 19 上唯一能写入
 * 任意字符的方式（`ACTION_SET_TEXT` 要 API 21）。
 *
 * 自动化流程（分配/结算）不经过这里：它走 [A11yService.performClick] 按**节点**
 * 操作——那一步会真的扣钱，坐标不够可靠。
 *
 * ## API 19 的约束
 *
 * `dispatchGesture` 要 API 24，所以这里也不做手势合成，那是 shell 的事。
 */
object A11yUiChannel {

    private const val TAG = "A11yUiChannel"

    /** 剪贴板条目标签。 */
    private const val CLIPBOARD_LABEL = "ntushower"

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
     * 4.4 上 ClipboardManager 构造需主线程 Looper，故由调用方确保不在主线程上直接
     * 调用；这里只做同步的剪贴板写入。
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

    /**
     * 找可写入的节点：优先带焦点的可编辑控件，退而取第一个可编辑节点。
     *
     * 调用方负责 recycle 返回值。`getChild` 返回的是独立对象，回收 child 不会让
     * 子树里的命中项失效。
     */
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
}
