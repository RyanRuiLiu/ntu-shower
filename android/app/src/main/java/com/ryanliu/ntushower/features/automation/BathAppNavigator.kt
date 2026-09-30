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

    // ── resId 特征（浴室 App 固定 UI 结构）──

    private const val RID_TV_BACKPREPAGE = "com.datalook.bath:id/tv_backprepage"
    private const val RID_TV_TITLE = "com.datalook.bath:id/tv_title"
    private const val RID_TV_BUTTON_NOCARD = "com.datalook.bath:id/tv_button_nocard"
    private const val RID_TV_BUTTON_NOCARD_STRIKE = "com.datalook.bath:id/tv_button_nocard_strike"
    private const val RID_ET_ID_CODE = "com.datalook.bath:id/et_id_code"
    private const val RID_ET_ID_PWD = "com.datalook.bath:id/et_id_pwd"
    private const val RID_TVB_ID_OK = "com.datalook.bath:id/tvb_id_ok"
    private const val RID_TV_NOWDATE = "com.datalook.bath:id/tv_nowdate"

    /** 喷头网格的容器。定位单个喷头要从它开始。 */
    private const val RID_GV_SPRINKLER = "com.datalook.bath:id/gv_sprinkler"

    /** 通知栏与最近任务面板的表面：它们压在最上层，热切前台拗不过，必须先关掉。 */
    private const val SYSTEM_UI_PACKAGE = "com.android.systemui"

    // ── 导航 ──

    /** 从任意页面回到 HOME；总预算 [budgetMs]。 */
    fun navigateToHome(a11y: A11yService, budgetMs: Long): Boolean {
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
     * 确保浴室 App 在前台。
     *
     * **SystemUI 要单独处理**：学生拉下通知栏或按了最近任务键时，前台包名是
     * `com.android.systemui`。这时直接 `launchBathApp`（走 `REORDER_TO_FRONT`）只会把
     * 浴室 App 的任务热切到前台，**通知栏窗口仍压在最上层**——于是自动化全程正常跑完，
     * 屏幕却始终是通知栏，全屏遮盖层也盖不住它（那是系统窗口，不归我们的图层管）。
     * 现场学生看到的不是「网络异常」提示图，而是一张没划上去的通知栏。
     *
     * `ACTION_CLOSE_SYSTEM_DIALOGS` 是 API 19 上不需要 root 的关闭手段。关掉之后要等
     * 一拍再拉起：广播是异步的，紧接着热切前台会把还没收起的面板又留在那里。
     */
    private fun ensureForeground(a11y: A11yService) {
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
        if (pkg == SYSTEM_UI_PACKAGE) {
            a11y.sendBroadcast(Intent(Intent.ACTION_CLOSE_SYSTEM_DIALOGS))
            A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
        }
        if (pkg != AppConfig.BATH_APP_PACKAGE) {
            a11y.launchBathApp()
        }
    }

    private fun clickBack(a11y: A11yService) {
        val root = a11y.rootInActiveWindow ?: return
        try {
            val back = findNode(root, RID_TV_BACKPREPAGE)
            if (back != null) {
                a11y.performClick(back)
                back.recycle()
            }
        } finally {
            root.recycle()
        }
    }

    // ── 等待 ──

    /** 等待页面到达指定状态；期间检测到浴室 App 离开前台自动重拉一次。 */
    fun waitForPage(a11y: A11yService, target: PageState, timeoutMs: Long): Boolean {
        val start = System.currentTimeMillis()
        var relaunched = false
        while (System.currentTimeMillis() - start < timeoutMs) {
            val mark = A11yService.pageChangeMark()
            if (BathAppPage.snapshot().state == target) return true
            if (!relaunched && !A11yService.bathAppInForeground) {
                a11y.launchBathApp()
                relaunched = true
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
            val grid = findNode(root, RID_GV_SPRINKLER) ?: return null
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
                val title = findTitleShallow(child)
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

    private fun findTitleShallow(parent: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (i in 0 until parent.childCount) {
            val child = parent.getChild(i) ?: continue
            try {
                if (matches(child.viewIdResourceName ?: "", RID_TV_TITLE)) {
                    return AccessibilityNodeInfo.obtain(child)
                }
                findTitleInGrandchildren(child)?.let { return it }
            } finally {
                child.recycle()
            }
        }
        return null
    }

    private fun findTitleInGrandchildren(child: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        for (j in 0 until child.childCount) {
            val gc = child.getChild(j) ?: continue
            try {
                if (matches(gc.viewIdResourceName ?: "", RID_TV_TITLE)) {
                    return AccessibilityNodeInfo.obtain(gc)
                }
            } finally {
                gc.recycle()
            }
        }
        return null
    }

    /** 点击无卡模式按钮（分配）。 */
    fun clickNoCardButton(a11y: A11yService): Boolean = clickInActiveWindow(a11y, RID_TV_BUTTON_NOCARD)

    /** 点击结算按钮。 */
    fun clickSettleButton(a11y: A11yService): Boolean = clickInActiveWindow(a11y, RID_TV_BUTTON_NOCARD_STRIKE)

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
        val idField = findNode(root, RID_ET_ID_CODE) ?: return false
        try {
            if (!a11y.performSetText(idField, certNo)) return false
        } finally {
            idField.recycle()
        }
        val pwdField = findNode(root, RID_ET_ID_PWD) ?: return false
        try {
            if (!a11y.performSetText(pwdField, password)) return false
        } finally {
            pwdField.recycle()
        }
        val ok = findNode(root, RID_TVB_ID_OK) ?: return false
        return try {
            a11y.performClick(ok)
        } finally {
            ok.recycle()
        }
    }

    /** 取 tv_nowdate 中心坐标（供 input tap 注入真实触摸清除网格选中态）。 */
    fun getDateViewCenter(a11y: A11yService): Pair<Int, Int>? {
        val root = a11y.rootInActiveWindow ?: return null
        try {
            val node = findNode(root, RID_TV_NOWDATE) ?: return null
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
