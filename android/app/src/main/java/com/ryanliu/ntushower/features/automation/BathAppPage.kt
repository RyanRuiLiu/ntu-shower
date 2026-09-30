// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.service.A11yService
import java.util.concurrent.CopyOnWriteArrayList

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

/** 一次识别结果：状态，以及该页面携带的提示文本（仅 HINT / VF_RESULT 非空）。 */
data class PageSnapshot(val state: PageState, val message: String = "")

/**
 * 页面状态的**唯一来源**：事件驱动地维护当前状态，并通知关心它的人。
 *
 * ## 为什么不是"每次问就重新算"
 *
 * 识别要遍历整棵节点树，而它现在有**三个消费方**——自动化流程、浴室 App 的退出监控、
 * 以及网页端按页面状态做展示。让每个消费方各自调用一次 `detectPage`，等于同一棵树被
 * 反复扫，而且每次都要等下一次轮询才知道状态变了。
 *
 * 改成推模型：无障碍事件到达时重算一次，状态**真的变了**才通知订阅者。消费方读
 * [snapshot] 是纯内存读，随时拿到的是当前状态而不是"上次轮询时的状态"。
 *
 * ## 节流
 *
 * **不做节流**，实测数据见 [refresh] 的注释：节点树只有 50 来个节点，界面是秒级刷新，
 * 全树重算的代价比 agent 自身的常驻开销还小一个量级。
 *
 * ## 线程
 *
 * [refresh] 由无障碍服务的回调线程调用（系统单线程派发），[snapshot] 与订阅回调可能
 * 来自任意线程，因此状态用 `@Volatile`、订阅表用 `CopyOnWriteArrayList`。
 */
object BathAppPage {

    private const val TAG = "BathAppPage"

    /** 资源名匹配用的 resId（浴室 App 固定 UI 结构）。 */
    private const val RID_GV_SPRINKLER = "com.datalook.bath:id/gv_sprinkler"
    private const val RID_LL_VERIFY_IDSERIAL = "com.datalook.bath:id/ll_verify_idserial"
    private const val RID_TV_HINT = "com.datalook.bath:id/tv_hint"
    private const val RID_TV_VF_RESULT = "com.datalook.bath:id/tv_vf_result"
    private const val RID_LL_BOOKING = "com.datalook.bath:id/ll_booking"
    private const val RID_LL_VERIFY_BOOKINGCODE = "com.datalook.bath:id/ll_verify_bookingcode"

    @Volatile
    private var cached = PageSnapshot(PageState.UNKNOWN)

    private val listeners = CopyOnWriteArrayList<(PageSnapshot) -> Unit>()

    /** 当前页面状态。纯内存读，任意线程可用。 */
    fun snapshot(): PageSnapshot = cached

    /**
     * 重算并（在状态变化时）通知订阅者。
     *
     * 由 [A11yService.onAccessibilityEvent] 在内容变化时调用。
     *
     * **不做节流**。实测过这台设备的账：节点树只有 50 来个节点，一次遍历约 10–30ms；
     * 而界面上的计时与时间显示是秒级刷新，也就是每秒一到两次事件。代价不到单核的
     * 百分之几，比 agent 自身的常驻开销还小一个量级。
     *
     * 真要做节流就必须配套补算——只丢不补会让状态永远停在旧值上，因为被丢掉的那个
     * 事件恰恰可能就是最后一次变化。为了一个测不出来的收益引入这层状态不划算。
     */
    fun refresh() {
        val startedAt = System.nanoTime() / 1_000_000
        val next = compute()
        AutomationTrace.recordRefresh(System.nanoTime() / 1_000_000 - startedAt, next.state.name)
        if (next == cached) return
        cached = next
        Log.d(TAG, "page -> ${next.state}${if (next.message.isEmpty()) "" else " (${next.message})"}")
        listeners.forEach { it(next) }
    }

    /**
     * 订阅页面状态变化；返回取消订阅的动作。
     *
     * 回调在无障碍回调线程上执行——**不要在里面做长操作**（遍历节点树、等 IO），
     * 那会拖住系统的事件派发。
     */
    fun observe(listener: (PageSnapshot) -> Unit): () -> Unit {
        listeners.add(listener)
        return { listeners.remove(listener) }
    }

    private fun compute(): PageSnapshot {
        val a11y = A11yService.instance ?: return PageSnapshot(PageState.UNKNOWN)
        val root = a11y.rootInActiveWindow ?: return PageSnapshot(PageState.UNKNOWN)
        try {
            val features = Features()
            scan(root, features)
            return features.toSnapshot()
        } finally {
            root.recycle()
        }
    }

    /**
     * 一次遍历中收集到的特征，以及由它判定页面状态的规则。
     *
     * 与遍历分开是为了**可测**：把一棵真实的节点树搬进 JVM 单测是不可能的（`AccessibilityNodeInfo`
     * 是系统类），但"见到哪些特征就该判成哪个状态"是纯逻辑，也是真正会出错的地方。
     */
    internal class Features {
        var hasHome = false
        var hasIdVerify = false
        var hasHint = false
        var hasVfResult = false
        var hasBooking = false
        var hasBookingCode = false
        var hintText: String? = null
        var vfResultText: String? = null

        /** 判定顺序即优先级：HOME 最前，因为它是最常见的稳定态。 */
        fun toSnapshot(): PageSnapshot {
            val state = when {
                hasHome -> PageState.HOME
                hasIdVerify -> PageState.ID_VERIFY
                hasHint -> PageState.HINT
                hasVfResult -> PageState.VF_RESULT
                hasBooking -> PageState.BOOKING
                hasBookingCode -> PageState.BOOKING_CODE
                else -> PageState.UNKNOWN
            }
            val message = when (state) {
                PageState.HINT -> hintText ?: ""
                PageState.VF_RESULT -> vfResultText ?: ""
                else -> ""
            }
            return PageSnapshot(state, message)
        }
    }

    /**
     * 单次 DFS 同时匹配全部特征，HOME 命中即整体收工。
     *
     * HOME 可以短路而其他状态不行：判定顺序里 HOME 最优先，一旦见到它，后面再出现什么
     * 都不会改变结论。反过来，先见到 `ID_VERIFY` 还要继续找 HOME——那是这个页面在切换
     * 过程中的常见中间态。
     */
    private fun scan(node: AccessibilityNodeInfo, f: Features) {
        val rid = node.viewIdResourceName ?: ""
        when (rid) {
            RID_GV_SPRINKLER -> {
                f.hasHome = true
                return
            }

            RID_LL_VERIFY_IDSERIAL -> f.hasIdVerify = true

            RID_TV_HINT -> {
                f.hasHint = true
                if (f.hintText == null) f.hintText = node.text?.toString()
            }

            RID_TV_VF_RESULT -> {
                f.hasVfResult = true
                if (f.vfResultText == null) f.vfResultText = node.text?.toString()
            }

            RID_LL_BOOKING -> f.hasBooking = true

            RID_LL_VERIFY_BOOKINGCODE -> f.hasBookingCode = true
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            try {
                scan(child, f)
            } finally {
                child.recycle()
            }
            if (f.hasHome) return
        }
    }
}
