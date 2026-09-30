// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle/obtain 在新版 SDK
// 中废弃，但在 19 上不回收会造成节点泄漏，是唯一正确路径。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.content.Intent
import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.service.A11yService

/**
 * 浴室 App 的导航与元素操作。
 *
 * 页面识别（[PageState] / [PageSnapshot]）住在 [BathAppPage]：它要同时服务自动化流程、
 * 浴室 App 的退出监控和网页端的按状态展示，是那份状态的唯一来源。这里只负责**动作**——
 * 等页面、回首页、找元素、点下去。
 *
 * resId 语义提取自对浴室 App 的实测（见 docs/ARCHITECTURE.md）。
 */
@Suppress("TooManyFunctions") // 页面导航原语集合：查找/操作/状态三组共享同一套资源 ID 与节点遍历上下文
object BathAppNavigator {

    // ── 导航 ──

    /** 从任意页面回到 HOME；总预算 [budgetMs]。 */
    fun navigateToHome(a11y: A11yService, budgetMs: Long): Boolean {
        // 已经在 HOME 就直接收工。ensureForeground 要读一次活动窗口的根节点（一次同步 IPC，
        // 实测 35~70ms），而 HOME 只可能是浴室 App 自己的页面——它在，就说明它在前台。
        // 每个流程都从"回首页"开始，而绝大多数时候本来就在首页，所以这一下省的是常态开销。
        //
        // 两个条件都要：快照可能是旧值（重算只在事件到达时发生），而 bathAppInForeground 由
        // 窗口状态事件维护，它才是"浴室 App 此刻在前台"的即时依据。
        if (A11yService.bathAppInForeground && BathAppPage.snapshot().state == PageState.HOME) return true
        ensureForeground(a11y)
        val start = System.currentTimeMillis()
        while (System.currentTimeMillis() - start < budgetMs) {
            // 标记取在检测之前：检测本身要遍历一遍节点树，那期间发生的变化也该算数
            val mark = A11yService.pageChangeMark()
            when (BathAppPage.snapshot().state) {
                PageState.HOME -> return true

                PageState.ID_VERIFY, PageState.BOOKING, PageState.BOOKING_CODE, PageState.UNKNOWN ->
                    clickBack(a11y)

                PageState.HINT, PageState.VF_RESULT -> Unit // 等待自动消失
            }
            A11yService.awaitPageChangeAfter(mark, RuntimeConfig.pollIntervalMs)
        }
        return false
    }

    /**
     * 确保浴室 App 在前台，并且没有系统窗口压在它上面。
     *
     * ## 为什么不能只热切前台
     *
     * 学生拉下通知栏、按了最近任务键，或者系统弹出自己的对话框（低电量、无响应、权限请求）
     * 时，前台都不是浴室 App。这时直接 `launchBathApp`（走 `REORDER_TO_FRONT`）只会把浴室
     * App 的任务热切到前台，**那些窗口仍压在最上层**——于是自动化全程正常跑完，屏幕却始终
     * 是它们，全屏遮盖层也盖不住（那是系统窗口，不归我们的图层管）。现场学生看到的不是
     * 「网络异常」提示图，而是一张没划上去的通知栏。
     *
     * `ACTION_CLOSE_SYSTEM_DIALOGS` 是 API 19 上不需要 root 的关闭手段，关的是通知栏、
     * 最近任务、电源菜单这一类系统对话框，对普通应用没有影响。关掉之后要等一拍再拉起：广播
     * 是异步的，紧接着热切前台会把还没收起的面板又留在那里。
     *
     * 判据按"是不是浴室 App"而不是"是不是某个具体系统包"：系统窗口不止 SystemUI 一家，
     * 逐个列举必然漏。而关系统对话框这个动作本身就是幂等的——没有可关的时候它什么都不做。
     *
     * 这不是"看见弹窗就找确定键点一下"那类试探：关面板与拉前台都是系统给出的确定动作，而
     * 认不出的页面一律走失败回报，不去猜它是什么、更不去点它上面的任何东西。
     */
    fun ensureForeground(a11y: A11yService) {
        val root = a11y.rootInActiveWindow
        if (root == null) {
            a11y.launchBathApp()
            return
        }
        val pkg = try {
            root.packageName?.toString() ?: ""
        } finally {
            root.recycle()
        }
        if (pkg == AppConfig.BATH_APP_PACKAGE) return
        a11y.sendBroadcast(Intent(Intent.ACTION_CLOSE_SYSTEM_DIALOGS))
        A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
        a11y.launchBathApp()
    }

    private fun clickBack(a11y: A11yService) {
        val root = a11y.rootInActiveWindow ?: return
        try {
            val back = findNode(root, BathAppResIds.BACK_TO_PREVIOUS)
            if (back != null) {
                a11y.performClick(back)
                back.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    // ── 等待 ──

    /**
     * 等待页面到达指定状态；期间只要浴室 App 不在前台就把它拉回来。
     *
     * 拉回来走 [ensureForeground]（含关系统面板），不是裸的 `launchBathApp`：离开前台的常见
     * 起因是学生拉下了通知栏或进了最近任务，那时前台是 SystemUI——热切前台拗不过压在最上面
     * 的系统面板，只有先把面板关掉才回得来。
     *
     * **不限制恢复次数**：反复拉面板的场面这套流程该扛住，而它本来就有总超时收口；[ensureForeground]
     * 在浴室 App 已在前台时什么都不做，重试不会空转。
     */
    fun waitForPage(a11y: A11yService, target: PageState, timeoutMs: Long): Boolean {
        val start = System.currentTimeMillis()
        while (System.currentTimeMillis() - start < timeoutMs) {
            val mark = A11yService.pageChangeMark()
            if (BathAppPage.snapshot().state == target) return true
            if (!A11yService.bathAppInForeground) {
                ensureForeground(a11y)
                A11yService.awaitPageChangeAfter(mark, RuntimeConfig.pollIntervalMs)
                continue
            }
            A11yService.awaitPageChangeAfter(mark, RuntimeConfig.pollIntervalMs)
        }
        return false
    }

    // ── 动作原语 ──

    /** HOME 页喷头网格中按标题文本定位指定编号的喷头项。 */
    fun findDoor(a11y: A11yService, doorNo: Int): AccessibilityNodeInfo? {
        val root = a11y.rootInActiveWindow ?: return null
        try {
            val grid = findNode(root, BathAppResIds.SPRINKLER_GRID) ?: return null
            try {
                return findDoorInGrid(grid, doorNo)
            } finally {
                grid.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    private fun findDoorInGrid(grid: AccessibilityNodeInfo, doorNo: Int): AccessibilityNodeInfo? {
        for (i in 0 until grid.childCount) {
            val child = grid.getChild(i) ?: continue
            try {
                val title = findTitle(child)
                try {
                    if (title != null && title.text?.toString() == doorNo.toString()) {
                        return AccessibilityNodeInfo.obtain(child)
                    }
                } finally {
                    // 匹配成功路径此前漏回收：注释强调过 19 上不回收会泄漏节点
                    title?.recycle()
                }
            } finally {
                child.recycle()
            }
        }
        return null
    }

    /**
     * 在喷头项的两层子节点里找标题控件（[BathAppResIds.DOOR_TITLE]）。
     *
     * **限深两层**而不是全树递归：喷头项的结构是「容器 → 标题容器 → 标题」，写浅了会漏，
     * 写深了则可能命中别处同名的控件。原先拆成 `findTitleShallow` 与
     * `findTitleInGrandchildren` 两个方法互相调用，实际表达的就是"先看子、再看孙"这一件事。
     */
    private fun findTitle(item: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (i in 0 until item.childCount) {
            val child = item.getChild(i) ?: continue
            val title = try {
                if (matches(child.viewIdResourceName ?: "", BathAppResIds.DOOR_TITLE)) {
                    AccessibilityNodeInfo.obtain(child)
                } else {
                    findDirectChild(child, BathAppResIds.DOOR_TITLE)
                }
            } finally {
                child.recycle()
            }
            if (title != null) return title
        }
        return null
    }

    /**
     * 在 [parent] 的**直接**子节点里找资源名为 [resId] 的控件。
     *
     * 与 [findNode] 的区别是不下钻：标题固定在上面那两层里，搜深了会命中别处同名的控件。
     */
    private fun findDirectChild(parent: AccessibilityNodeInfo, resId: String): AccessibilityNodeInfo? {
        for (i in 0 until parent.childCount) {
            val child = parent.getChild(i) ?: continue
            try {
                if (matches(child.viewIdResourceName ?: "", resId)) {
                    return AccessibilityNodeInfo.obtain(child)
                }
            } finally {
                child.recycle()
            }
        }
        return null
    }

    /** 点击无卡模式按钮（分配）。 */
    fun clickNoCardButton(a11y: A11yService): Boolean = clickInActiveWindow(a11y, BathAppResIds.NOCARD_BUTTON)

    /** 点击结算按钮。 */
    fun clickSettleButton(a11y: A11yService): Boolean = clickInActiveWindow(a11y, BathAppResIds.SETTLE_BUTTON)

    /**
     * 验证页输入账号密码并确认。
     *
     * 凭证是**经剪贴板**粘贴进去的（API 19 没有 `ACTION_SET_TEXT`），所以从写进剪贴板
     * 那一刻起，任何一条失败路径都必须负责清掉它——`clearClipboard` 挂在这个方法唯一的
     * 出口上，而不是挂在成功分支里。Android 4.4 的剪贴板任何应用可读，而这台设备就挂在
     * 公共浴室的墙上。
     */
    fun inputCredentialsAndConfirm(a11y: A11yService, certNo: String, password: String): Boolean {
        val root = a11y.rootInActiveWindow ?: return false
        try {
            return try {
                fillCredentials(a11y, root, certNo, password)
            } finally {
                a11y.clearClipboard()
            }
        } finally {
            root.recycle()
        }
    }

    /** 逐个字段写入并点确认。任一环节失败即返回 false，由调用方负责收尾。 */
    private fun fillCredentials(
        a11y: A11yService,
        root: AccessibilityNodeInfo,
        certNo: String,
        password: String,
    ): Boolean {
        // 三个控件一趟收齐：原先是三个 findNode 各走一遍整棵树，而同一棵树走三遍是白花的
        // ——每次 getChild 都是一次跨进程调用。真机上这一段量到 211ms，其中约 90ms 花在这
        // 三遍遍历上。
        val fields = findNodes(
            root,
            setOf(BathAppResIds.ID_CODE_FIELD, BathAppResIds.ID_PWD_FIELD, BathAppResIds.ID_CONFIRM_BUTTON),
        )
        try {
            val idField = fields[BathAppResIds.ID_CODE_FIELD] ?: return false
            if (!a11y.performSetText(idField, certNo)) return false
            val pwdField = fields[BathAppResIds.ID_PWD_FIELD] ?: return false
            if (!a11y.performSetText(pwdField, password)) return false
            val ok = fields[BathAppResIds.ID_CONFIRM_BUTTON] ?: return false
            return a11y.performClick(ok)
        } finally {
            fields.values.forEach { it.recycle() }
        }
    }

    /**
     * 一趟遍历收集多个资源 ID 对应的控件；没找到的不放进结果。
     *
     * 找齐就停：验证页那三个控件是并列的兄弟，不必为其中一个走完整棵树。
     */
    private fun findNodes(root: AccessibilityNodeInfo, resIds: Set<String>): Map<String, AccessibilityNodeInfo> {
        val found = HashMap<String, AccessibilityNodeInfo>()
        collectNodes(root, resIds, found)
        return found
    }

    private fun collectNodes(
        node: AccessibilityNodeInfo,
        resIds: Set<String>,
        found: MutableMap<String, AccessibilityNodeInfo>,
    ) {
        if (found.size == resIds.size) return
        val rid = node.viewIdResourceName ?: ""
        if (rid in resIds && rid !in found) found[rid] = AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                collectNodes(child, resIds, found)
            } finally {
                child.recycle()
            }
        }
    }

    /** 取 tv_nowdate 中心坐标（供 input tap 注入真实触摸清除网格选中态）。 */
    fun getDateViewCenter(a11y: A11yService): Pair<Int, Int>? {
        val root = a11y.rootInActiveWindow ?: return null
        try {
            val node = findNode(root, BathAppResIds.DATE_VIEW) ?: return null
            try {
                val rect = Rect()
                node.getBoundsInScreen(rect)
                return Pair(rect.centerX(), rect.centerY())
            } finally {
                node.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    private fun clickInActiveWindow(a11y: A11yService, resId: String): Boolean {
        val root = a11y.rootInActiveWindow ?: return false
        try {
            val node = findNode(root, resId) ?: return false
            return try {
                a11y.performClick(node)
            } finally {
                node.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    private fun findNode(root: AccessibilityNodeInfo, resId: String): AccessibilityNodeInfo? {
        if (matches(root.viewIdResourceName ?: "", resId)) return AccessibilityNodeInfo.obtain(root)
        for (i in 0 until root.childCount) {
            val child = root.getChild(i) ?: continue
            val result = findNode(child, resId)
            child.recycle()
            if (result != null) return result
        }
        return null
    }

    /**
     * 节点资源名匹配。
     *
     * resId 一律是全名（`com.datalook.bath:id/xxx`），与 viewIdResourceName 可直接比较；
     * 因此不做后缀匹配——给全名再加前缀/后缀的条件永远不会成立。
     */
    private fun matches(nodeRid: String, resId: String): Boolean = nodeRid == resId
}
