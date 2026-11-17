// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。本文件遍历节点，必须逐个回收。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.a11y.A11yNodeSource

/**
 * 界面节点树导出，格式与 `uiautomator dump` 完全一致。
 *
 * ## 为什么照抄那个格式
 *
 * `uiautomator dump` 是 Android 官方的界面层级输出（`UiDevice.dumpWindowHierarchy`
 * 同一套序列化），它的字段名、`index` 语义、`bounds` 写法都有既定含义：
 *
 * - `index` —— **同一父节点下的第几个子节点**，不是全树的序号
 * - 层级用**嵌套**表达，没有 `depth` 字段
 * - `bounds="[left,top][right,bottom]"`
 * - 属性名带连字符：`resource-id` / `content-desc` / `long-clickable`
 *
 * 自行另定一套 JSON，只有在唯一的读者是自己时才成立；任何按 UiAutomator 写过的
 * 工具、文档与经验都会对不上。
 *
 * ## 为什么不用 `uiautomator dump`
 *
 * 节点树只有本文件这一个实现，root shell 那条不提供，两条理由：
 *
 * 1. 它要等 UI 空闲才执行，而浴室 APP 有跑马灯持续动画——在**它的界面上**永远
 *    失败（实测 `ERROR: could not get idle state.`）。注意只在浴室 APP 上如此，
 *    换到设置、拨号盘这类静态界面是能跑通的。
 * 2. 它经 `UiAutomation` 接入系统无障碍框架。真机上出现过：在一轮界面调试中用它
 *    取过几次节点之后，设备的无障碍总开关 `accessibility_enabled` 变成 0 且拒绝
 *    写回，**全部**无障碍服务一起失效（本应用与同机旧系统同时瘫痪）。
 *    因果未能坐实（该操作此前多次执行并无异常），但这条通路对设备的风险远大于
 *    它带来的便利，因此不采用。总开关的恢复办法见 `A11yGuard` 的类注释。
 */
object UiTreeCodec {

    /** XML 转义：属性值里的这几个字符必须替换，否则产出的 XML 无法解析。 */
    private val ESCAPES = mapOf(
        "&" to "&amp;",
        "<" to "&lt;",
        ">" to "&gt;",
        "\"" to "&quot;",
    )

    /**
     * 导出当前窗口，返回与 `uiautomator dump` 同格式的 XML。
     *
     * 单次导出的节点上限由调用方传入（CommandScheduler 的 UI_DUMP_MAX_NODES）。
     * 无法读取根节点时返回 null——调用方据此报错，而不是返回空文档让远端
     * 以为"这个界面真的什么都没有"。
     */
    fun export(source: A11yNodeSource, maxNodes: Int): String? {
        val root = source.activeWindowRoot() ?: return null
        try {
            val out = StringBuilder(4096)
            out.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
            out.append("<hierarchy rotation=\"").append(source.screenRotation()).append("\">\n")
            var budget = maxNodes
            for (index in 0 until root.childCount) {
                val child = root.getChild(index) ?: continue
                if (budget > 0) {
                    budget = writeNode(out, child, depth = 1, budget = budget, siblingIndex = index)
                }
                child.recycle()
            }
            if (budget == 0) {
                // 静默截断会让远端把"没写下的"当成"界面上没有的"——与上面那句"不返回空文档"
                // 是同一条原则。标注写成 XML 注释：它必须能被解析器忽略（不是树的一部分），
                // 又要人一眼看得见。判据取 budget 归零即可；"节点数恰好等于上限"会被误报，
                // 但那与"截断了却不说"相比是可以接受的。
                out.append("  <!-- 已达节点上限 ").append(maxNodes).append("，其余内容省略 -->\n")
            }
            out.append("</hierarchy>\n")
            return out.toString()
        } finally {
            root.recycle()
        }
    }

    /**
     * 递归写一个节点及其子树，返回剩余额度。
     *
     * 递归而非显式栈：需要的嵌套缩进本来就是树形结构，递归与输出形状同构。
     * 深度由界面层级决定（实测 5–6 层），不会深到爆栈。
     *
     * `siblingIndex` 即 `index` 属性的取值——**同级中的序号**，与 UiAutomator 一致。
     * 序号取 `getChild` 的**下标本身**，不是"过滤掉 null 之后的第几个"：节点失效时
     * `getChild` 会返回 null，按过滤后的位置编号会让其后的兄弟全部错位，而对不上
     * 序号的树比没有序号更糟。
     */
    private fun writeNode(
        out: StringBuilder,
        node: AccessibilityNodeInfo,
        depth: Int,
        budget: Int,
        siblingIndex: Int,
    ): Int {
        if (budget <= 0) return 0
        val indent = "  ".repeat(depth)

        out.append(indent).append("<node index=\"").append(siblingIndex).append('"')
        for ((name, value) in attributesOf(node)) {
            out.append(' ').append(name).append("=\"").append(escape(value)).append('"')
        }

        val children = (0 until node.childCount).mapNotNull { index -> node.getChild(index)?.let { index to it } }
        if (children.isEmpty()) {
            out.append(" />\n")
            return budget - 1
        }

        out.append(">\n")
        var left = budget - 1
        for ((index, child) in children) {
            if (left > 0) {
                left = writeNode(out, child, depth + 1, left, index)
            }
            child.recycle()
        }
        out.append(indent).append("</node>\n")
        return left
    }

    /**
     * 节点的属性，顺序与字段名照 `uiautomator dump`。
     *
     * `checkable` / `password` 在 API 19 的 `AccessibilityNodeInfo` 上没有对应
     * 读取方法，固定写 false——它们只影响客户端展示，不影响定位。
     */
    private fun attributesOf(node: AccessibilityNodeInfo): List<Pair<String, String>> {
        val rect = Rect()
        // API 19 只有带参数的 getBoundsInScreen
        node.getBoundsInScreen(rect)
        return listOf(
            "text" to (node.text?.toString() ?: ""),
            "resource-id" to (node.viewIdResourceName ?: ""),
            "class" to (node.className?.toString() ?: ""),
            "package" to (node.packageName?.toString() ?: ""),
            "content-desc" to (node.contentDescription?.toString() ?: ""),
            "checkable" to "false",
            "checked" to node.isChecked.toString(),
            "clickable" to node.isClickable.toString(),
            "enabled" to node.isEnabled.toString(),
            "focusable" to node.isFocusable.toString(),
            "focused" to node.isFocused.toString(),
            "scrollable" to node.isScrollable.toString(),
            "long-clickable" to node.isLongClickable.toString(),
            "password" to "false",
            "selected" to node.isSelected.toString(),
            "bounds" to "[${rect.left},${rect.top}][${rect.right},${rect.bottom}]",
        )
    }

    /**
     * `hierarchy` 的 `rotation` 属性由 [A11yNodeSource.screenRotation] 提供——
     * 取 `Display.getRotation()`，官方 `uiautomator dump` 的语义就是它。
     */

    private fun escape(raw: String): String {
        if (raw.none { it.toString() in ESCAPES }) return raw
        val out = StringBuilder(raw.length + 16)
        for (ch in raw) {
            val replacement = ESCAPES[ch.toString()]
            out.append(replacement ?: ch.toString())
        }
        return out.toString()
    }
}
