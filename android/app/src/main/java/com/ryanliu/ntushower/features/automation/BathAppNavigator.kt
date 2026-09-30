// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle/obtain 在新版 SDK
// 中废弃，但在 19 上不回收会造成节点泄漏，是唯一正确路径。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.service.A11yService

/** 浴室 App 页面状态（resId 特征判定）。 */
enum class PageState {
    HOME, // 喷头选择首页
    ID_VERIFY, // 账号密码验证页
    HINT, // 提示页（短暂停留后自动回 HOME）
    VF_RESULT, // 验证结果页（短暂停留后自动回 HOME）
    BOOKING, // 预约页
    BOOKING_CODE, // 预约码输入页
    UNKNOWN,
}

/**
 * 浴室 App 页面导航与元素操作。
 *
 * 页面判定与 resId 语义提取自对浴室 App 的实测（见 docs/ARCHITECTURE.md）；
 * 页面检测用单次 DFS 同时匹配全部特征，HOME 命中即短路。
 */
@Suppress("TooManyFunctions") // 页面导航原语集合：查找/操作/状态三组共享同一套资源 ID 与节点遍历上下文
object BathAppNavigator {

    // ── resId 特征（浴室 App 固定 UI 结构）──

    private const val RID_GV_SPRINKLER = "com.datalook.bath:id/gv_sprinkler"
    private const val RID_LL_VERIFY_IDSERIAL = "com.datalook.bath:id/ll_verify_idserial"
    private const val RID_TV_HINT = "com.datalook.bath:id/tv_hint"
    private const val RID_TV_VF_RESULT = "com.datalook.bath:id/tv_vf_result"
    private const val RID_LL_BOOKING = "com.datalook.bath:id/ll_booking"
    private const val RID_LL_VERIFY_BOOKINGCODE = "com.datalook.bath:id/ll_verify_bookingcode"
    private const val RID_TV_BACKPREPAGE = "com.datalook.bath:id/tv_backprepage"
    private const val RID_TV_TITLE = "com.datalook.bath:id/tv_title"
    private const val RID_TV_BUTTON_NOCARD = "com.datalook.bath:id/tv_button_nocard"
    private const val RID_TV_BUTTON_NOCARD_STRIKE = "com.datalook.bath:id/tv_button_nocard_strike"
    private const val RID_ET_ID_CODE = "com.datalook.bath:id/et_id_code"
    private const val RID_ET_ID_PWD = "com.datalook.bath:id/et_id_pwd"
    private const val RID_TVB_ID_OK = "com.datalook.bath:id/tvb_id_ok"
    private const val RID_TV_NOWDATE = "com.datalook.bath:id/tv_nowdate"

    private data class PageInfo(
        var hasHome: Boolean = false,
        var hasIdVerify: Boolean = false,
        var hasHint: Boolean = false,
        var hasVfResult: Boolean = false,
        var hasBooking: Boolean = false,
        var hasBookingCode: Boolean = false,
        var hintText: String? = null,
        var vfResultText: String? = null,
    )

    /** 页面识别结果：状态，以及该页面的提示文本（仅 HINT/VF_RESULT 非空）。 */
    data class PageSnapshot(val state: PageState, val message: String = "")

    /** 单次 DFS 同时检测页面状态与提示文本。 */
    fun detectPage(): PageSnapshot {
        val a11y = A11yService.instance ?: return PageSnapshot(PageState.UNKNOWN)
        val root = a11y.rootInActiveWindow ?: return PageSnapshot(PageState.UNKNOWN)
        try {
            val info = PageInfo()
            scanNode(root, info)
            val state = when {
                info.hasHome -> PageState.HOME
                info.hasIdVerify -> PageState.ID_VERIFY
                info.hasHint -> PageState.HINT
                info.hasVfResult -> PageState.VF_RESULT
                info.hasBooking -> PageState.BOOKING
                info.hasBookingCode -> PageState.BOOKING_CODE
                else -> PageState.UNKNOWN
            }
            val text = when (state) {
                PageState.HINT -> info.hintText ?: ""
                PageState.VF_RESULT -> info.vfResultText ?: ""
                else -> ""
            }
            return PageSnapshot(state, text)
        } finally {
            root.recycle()
        }
    }

    private fun scanNode(node: AccessibilityNodeInfo, info: PageInfo) {
        val rid = node.viewIdResourceName ?: ""
        when {
            matches(rid, RID_GV_SPRINKLER) -> {
                info.hasHome = true
                return
            }

            matches(rid, RID_LL_VERIFY_IDSERIAL) -> info.hasIdVerify = true

            matches(rid, RID_TV_HINT) -> {
                info.hasHint = true
                if (info.hintText == null) info.hintText = node.text?.toString()
            }

            matches(rid, RID_TV_VF_RESULT) -> {
                info.hasVfResult = true
                if (info.vfResultText == null) info.vfResultText = node.text?.toString()
            }

            matches(rid, RID_LL_BOOKING) -> info.hasBooking = true

            matches(rid, RID_LL_VERIFY_BOOKINGCODE) -> info.hasBookingCode = true
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            scanNode(child, info)
            child.recycle()
            if (info.hasHome) return
        }
    }

    // ── 导航 ──

    /** 从任意页面回到 HOME；总预算 [budgetMs]。 */
    fun navigateToHome(a11y: A11yService, budgetMs: Long): Boolean {
        ensureForeground(a11y)
        val start = System.currentTimeMillis()
        while (System.currentTimeMillis() - start < budgetMs) {
            when (detectPage().state) {
                PageState.HOME -> return true

                PageState.ID_VERIFY, PageState.BOOKING, PageState.BOOKING_CODE, PageState.UNKNOWN ->
                    clickBack(a11y)

                PageState.HINT, PageState.VF_RESULT -> Unit // 等待自动消失
            }
            A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
        }
        return false
    }

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
            if (detectPage().state == target) return true
            if (!relaunched && !A11yService.bathAppInForeground) {
                a11y.launchBathApp()
                relaunched = true
                A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
                continue
            }
            A11yService.awaitPageChange(RuntimeConfig.pollIntervalMs)
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

    /** 验证页输入账号密码并确认。 */
    fun inputCredentialsAndConfirm(a11y: A11yService, certNo: String, password: String): Boolean {
        val root = a11y.rootInActiveWindow ?: return false
        try {
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
                a11y.clearClipboard()
            }
        } finally {
            root.recycle()
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
