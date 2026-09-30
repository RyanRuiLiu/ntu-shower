// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。本文件取到的节点都要回收。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.service

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
 * 任意字符的方式（`ACTION_SET_TEXT` 要 API 21）。它同时也是**最快**的一条：`input`
 * 每次执行都要起一个 `app_process`（ART 启动 + 加载 framework，几百毫秒），而剪贴板
 * 与 `ACTION_PASTE` 都是纯 IPC。
 *
 * 自动化流程（分配/结算）不经过这里：它走 [A11yService.performSetText] 按**节点**
 * 写入，那条路会真的扣钱，需要精确落到 `et_id_code` / `et_id_pwd` 上。
 *
 * ## API 19 的约束
 *
 * `dispatchGesture` 要 API 24，所以这里也不做手势合成，那是 shell 的事。
 */
object A11yUiChannel {

    /**
     * 向当前焦点控件输入文本；无焦点时取第一个可编辑节点。
     *
     * 写入本身交给 [A11yService.performSetText]：那一份已经处理了 4.4 上
     * 「ClipboardManager 必须在主线程构造」这条约束（经 MainThread 投递并同步等待），
     * 而它也是自动化流程正在用的同一条路径。这里再写一遍，就多出一条不受那条约束
     * 保护的路——而调用方跑在后台线程，正好踩中。
     *
     * 写进去的内容不留在剪贴板里：远程帮人打字可能是任何东西，没理由让它留在那里。
     */
    fun typeIntoFocus(service: A11yService, text: String): Boolean {
        val root = service.rootInActiveWindow ?: return false
        try {
            val target = findFocusOrEditable(root) ?: return false
            if (target == root) return false
            return try {
                service.performSetText(target, text)
            } finally {
                target.recycle()
                service.clearClipboard()
            }
        } finally {
            root.recycle()
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
