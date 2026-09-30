// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.service.A11yService
import java.util.concurrent.CopyOnWriteArrayList

private const val TAG = "BathAppPage"

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
 * 探针找到的控件。
 *
 * "找到了"与"文本是什么"合成一次返回，是因为分开查就是两次系统调用——而系统调用正是
 * 这套识别的全部代价。控件在却没填字时 [text] 是空串，不会让 null 漏到调用方（那会在
 * 拼提示语时变成字符串 "null" 显示给学生）。
 */
internal class PageElement(val text: String)

/**
 * 页面探针：按资源 ID 找控件。
 *
 * 判定规则（谁优先、命中谁）和"怎么找控件"分在两处，是因为前者才会写错，而后者是系统
 * API——`AccessibilityNodeInfo` 在 JVM 单测里造不出来。有了这层接口，规则就能用一张内存
 * 表测透。
 */
internal interface PageProbe {

    /** 找该资源 ID 的控件；不存在，或查询期间节点失效，都返回 null。 */
    fun find(resId: String): PageElement?
}

/**
 * 页面判定规则：见到哪些控件就判成哪个状态。
 *
 * **判定顺序即优先级**。首页排在最前，一是因为它是稳定态、其余特征可能只是页面切换过程
 * 中的残留；二是因为查询是懒的——命中即返回，后面的控件根本不查，于是稳态下每次识别只
 * 花一次查询。这个"只查一次"不是顺手写成的，是应对下面这组实测数字的唯一手段：
 *
 * 浴室 App 首页挂着跑马灯（`tvMarqueeViewHint`）与时钟（`tv_time`），它们让界面**每秒产生
 * 16 次**内容变化事件；而按层下钻取特征要跨进程取 52 个节点，单次 41.6ms——合起来常驻
 * 吃掉 0.68 个核，比 agent 其余全部开销还大一个量级。按 ID 查是让系统在服务侧一次遍历
 * 完成，稳态下一次调用即可。
 */
internal object PageRules {

    private const val RID_GV_SPRINKLER = "com.datalook.bath:id/gv_sprinkler"
    private const val RID_LL_VERIFY_IDSERIAL = "com.datalook.bath:id/ll_verify_idserial"
    private const val RID_TV_HINT = "com.datalook.bath:id/tv_hint"
    private const val RID_TV_VF_RESULT = "com.datalook.bath:id/tv_vf_result"
    private const val RID_LL_BOOKING = "com.datalook.bath:id/ll_booking"
    private const val RID_LL_VERIFY_BOOKINGCODE = "com.datalook.bath:id/ll_verify_bookingcode"

    fun decide(probe: PageProbe): PageSnapshot {
        if (probe.find(RID_GV_SPRINKLER) != null) return PageSnapshot(PageState.HOME)
        // 停在 ID_VERIFY 上说明还在等输入，要压过下面两个"一闪过"的提示页
        if (probe.find(RID_LL_VERIFY_IDSERIAL) != null) return PageSnapshot(PageState.ID_VERIFY)
        probe.find(RID_TV_HINT)?.let { return PageSnapshot(PageState.HINT, it.text) }
        probe.find(RID_TV_VF_RESULT)?.let { return PageSnapshot(PageState.VF_RESULT, it.text) }
        if (probe.find(RID_LL_BOOKING) != null) return PageSnapshot(PageState.BOOKING)
        if (probe.find(RID_LL_VERIFY_BOOKINGCODE) != null) return PageSnapshot(PageState.BOOKING_CODE)
        return PageSnapshot(PageState.UNKNOWN)
    }
}

/**
 * 页面状态的**唯一来源**：事件驱动地维护当前状态，并通知关心它的人。
 *
 * ## 为什么不是"每次问就重新算"
 *
 * 识别要问一次系统（见 [PageRules] 里那组数字），而它现在有**三个消费方**——自动化流程、
 * 浴室 App 的退出监控、以及网页端按页面状态做展示。让每个消费方各自算一次，等于同一份
 * 答案被反复问，而且每次都要等下一次事件才知道状态变了。
 *
 * 改成推模型：无障碍事件到达时重算一次，状态**真的变了**才通知订阅者。消费方读
 * [snapshot] 是纯内存读，随时拿到的是当前状态，而不是"上次轮询时的状态"。
 *
 * ## 节流
 *
 * 不做。事件的密度由浴室 App 的动画决定（实测 16 次/秒），不是由我们决定的；把单次代价
 * 压到一次查询之后，这些事件合起来也远低于一个核的百分之几。真要再省就得丢事件，而被丢
 * 掉的那一个恰恰可能就是最后一次变化——状态会永远停在旧值上，代价远大于那点 CPU。
 *
 * ## 线程
 *
 * [refresh] 由无障碍服务的回调线程调用（系统单线程派发），[snapshot] 与订阅回调可能来自
 * 任意线程，因此状态用 `@Volatile`、订阅表用 `CopyOnWriteArrayList`。
 */
object BathAppPage {

    @Volatile
    private var cached = PageSnapshot(PageState.UNKNOWN)

    private val listeners = CopyOnWriteArrayList<(PageSnapshot) -> Unit>()

    /** 当前页面状态。纯内存读，任意线程可用。 */
    fun snapshot(): PageSnapshot = cached

    /** 重算并（在状态变化时）通知订阅者。 */
    fun refresh() {
        val startedAt = System.nanoTime() / 1_000_000
        val next = compute()
        AutomationTrace.recordRefresh(System.nanoTime() / 1_000_000 - startedAt, next.state.name, next != cached)
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
            return PageRules.decide(A11yProbe(root))
        } finally {
            root.recycle()
        }
    }
}

/**
 * 探针的真实现：把"按 ID 找控件"交给系统侧完成。
 *
 * 为什么不用逐层 `getChild` 下钻：整棵树 52 个节点，而首页那盏跑马灯让它每秒产生十几次变
 * 化——按层下钻就是每秒几百次跨进程调用，实测单次识别 41.6ms、常驻 0.68 个核。按 ID 查
 * 是一次调用换一次系统侧遍历，稳态下（首页命中即返回）总共只花一次。
 */
private class A11yProbe(private val root: AccessibilityNodeInfo) : PageProbe {

    override fun find(resId: String): PageElement? {
        val found = try {
            root.findAccessibilityNodeInfosByViewId(resId)
        } catch (e: RuntimeException) {
            // 查询途中页面切换会让节点失效并抛出来。认不出状态就报 UNKNOWN，调用方那边有
            // 「连续几次认不出才算失败」的宽限，不会被这一下打断流程。
            Log.w(TAG, "find $resId failed: ${e.message}")
            return null
        }
        if (found == null || found.isEmpty()) return null
        // 同一个 ID 可能有多个控件，判定只认第一个；剩下的当场回收，否则就是节点泄漏
        val head = found[0]
        for (index in 1 until found.size) found[index].recycle()
        try {
            return PageElement(head.text?.toString() ?: "")
        } finally {
            head.recycle()
        }
    }
}
