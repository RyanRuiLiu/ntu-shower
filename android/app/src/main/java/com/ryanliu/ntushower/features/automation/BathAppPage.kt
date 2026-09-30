// 设备 ROM 冻结在 Android 4.4（API 19）：AccessibilityNodeInfo.recycle 在新版 SDK 中废弃，
// 但在 19 上不回收会造成节点泄漏。
@file:Suppress("DEPRECATION")

package com.ryanliu.ntushower.features.automation

import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.ryanliu.ntushower.service.A11yService
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicBoolean

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
 * 一次内容变化事件里**不必再问系统**就能拿到的字段。
 *
 * 全是随事件一起送来的本地数据。`AccessibilityEvent.getSource()` 不在其列——那要触发一次
 * IPC 去系统侧取节点，而这类事件每秒来十几次。
 *
 * 采它的用途是量出"哪些事件根本不会改变页面状态"：浴室 App 首页挂着跑马灯与时钟，界面每秒
 * 变十几次，每次都付一次窗口快照的钱，而绝大多数什么都没算出来。要过滤就得先知道页面**真正
 * 切换**时那几个事件长什么样，否则只能靠猜。
 */
internal data class ContentEvent(val className: String, val text: String, val contentChangeTypes: Int) {

    /** 统计用的短标识：类名只留末段、文本截断，避免一行日志被一条公告撑爆。 */
    fun describe(): String {
        val shortClass = className.substringAfterLast('.')
        return "$shortClass|$contentChangeTypes|${text.take(TEXT_IN_DESCRIPTION)}"
    }

    private companion object {
        const val TEXT_IN_DESCRIPTION = 24
    }
}

/**
 * 探针找到的控件。
 *
 * 只需要文本一项，做成类而不是直接返回 `String?`，是为了让"控件在、但没填字"与"控件不在"
 * 分得开：前者是空串，后者是 null。合并成一个可空字符串的话，`tv_hint` 存在但文本为空会被
 * 判成"没有这个页面"，流程接着去点一个此刻不存在的按钮。
 */
internal class PageElement(val text: String)

/**
 * 页面探针：按资源 ID 找控件。
 *
 * 判定规则（谁优先、命中谁）和"怎么找控件"分在两处，是因为前者才会写错，而后者是系统
 * API——`AccessibilityNodeInfo` 在 JVM 单测里造不出来。有了这层接口，规则就能用一张内存
 * 表测透，而真实现那边则可以自由换取数方式（这一轮就换过一次，靠对比实测数据定的去向）。
 */
internal interface PageProbe {

    /** 找该资源 ID 的控件；不存在时返回 null。 */
    fun find(resId: String): PageElement?
}

/**
 * 页面判定规则：见到哪些控件就判成哪个状态。
 *
 * **判定顺序即优先级**。首页排在最前，一是因为它是稳定态、其余特征可能只是页面切换过程
 * 中的残留；二是因为查询是懒的——命中即返回，后面的控件根本不查。
 *
 * 懒查询在[探针](A11yProbe)那一侧才真正省钱：它把整棵树走一遍建好表，判定问第一句时表已
 * 经在了，所以稳态下（首页命中即返回）虽然只问一句，代价也只付一次遍历。
 *
 * 这个"只走一遍"是这套识别唯一能压的变量：浴室 App 首页挂着跑马灯（`tvMarqueeViewHint`）
 * 与 7 个计时文本（`tv_time`，每个喷头一个），界面每秒产生五十来条内容变化。其中与页面判定
 * 无关的绝大部分由 [PageEventRules] 挡在门外，剩下的每次都要重算一遍。
 *
 * 单次识别的耗时构成由 [AutomationTrace] 记录（`avg` 是总耗时，`qavg` 是取特征那一段）：
 * 总耗时里含着"取活动窗口根节点"这笔与树大小无关的开销，两段分开才看得出该继续砍哪一段
 * ——只看总耗时会得出错误的结论。这一轮就是这么发现"按 ID 查"反而更贵的。
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
 * 事件筛选：决定一次内容变化值不值得重算。
 *
 * ## 判据是量出来的
 *
 * 真机跑出来的分布（`automation.log` 的 `ev` 行，每行是一个重算窗口内各来源的条数）：
 *
 * | 事件 | 稳态 | 页面切换时 |
 * |---|---|---|
 * | `TextView` + 纯文本 | 168 条/窗口 | 只在噪声里 |
 * | `GridView` + 子树 | 44 条/窗口 | 只在噪声里 |
 * | `LinearLayout` + 未指定 | 0 | 5 条 |
 * | `LinearLayout` + 子树 | 0 | 2 条 |
 * | `TextView` + 未指定 | 0 | 1 条 |
 *
 * 稳态那两类撑着全部分母（每秒五十来条），而它们**一次都没让页面状态变过**——`changed` 在
 * 几十个窗口里恒为 0。来源就是首页那 7 个计时文本与一条跑马灯，每个每秒重设文本。切换页面
 * 那三类只在真实切换的窗口里出现，与稳态两类完全不重叠。
 *
 * ## 为什么要类名和变化类型两个维度
 *
 * 只看变化类型不够：`GridView` 报的同样是子树变化，但它自身的存在与否由首页决定，它变的是
 * 列表项。只看类名也不够：`TextView` 既报纯文本（计时、跑马灯），也在页面结构变化时报未指定。
 *
 * ## 保守的那一侧
 *
 * 判据只写"哪些**不**值得算"，其余一律算。反过来（列出值得算的）一旦漏掉某种没量到的事件，
 * 状态就会永远停在旧值上，而这种错误看不出来——界面照常工作，只是状态是陈的。这里的例外
 * 只有两类，真漏了也只是多算几次。
 */
internal object PageEventRules {

    /** `AccessibilityEvent.CONTENT_CHANGE_TYPE_TEXT`。 */
    private const val TEXT_ONLY = 2

    /** `AccessibilityEvent.CONTENT_CHANGE_TYPE_SUBTREE`。 */
    private const val SUBTREE = 1

    private const val TEXT_VIEW = "TextView"
    private const val GRID_VIEW = "GridView"

    /**
     * 这次事件是否可能改变页面状态。
     *
     * 变更类型按**整体相等**判断，不按位包含：只有"纯粹是文本变化"才免算，任何组合
     * （子树加文本）都算。类名用后缀匹配，于是 `TextView` 的子类也一并覆盖。
     */
    fun mayChangePage(event: ContentEvent): Boolean {
        if (event.contentChangeTypes == TEXT_ONLY && event.className.endsWith(TEXT_VIEW)) return false
        if (event.contentChangeTypes == SUBTREE && event.className.endsWith(GRID_VIEW)) return false
        return true
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
 * ## 哪些事件不值得算
 *
 * 事件密度由浴室 App 的界面决定，不由我们决定：首页挂着 7 个计时文本与一条跑马灯，实测每秒
 * 产生五十来条内容变化。但其中 95% 以上**一次都不会改变页面状态**——几十个统计窗口里
 * `changed` 恒为 0。
 *
 * 这部分按事件本身的特征筛掉，判据与实测分布见 [PageEventRules]。**不是按时间节流**：那是
 * 另一种东西，它会把一个恰好落在窗口内的变化推迟到窗口结束，而真的变化一到就该立刻重算。
 * 这里筛掉的是"确定与页面判定无关"的事件，没有等待窗口这回事。
 *
 * ## 线程
 *
 * **重算跑在自己的线程上，不在调用方的线程上。** 这一点不是洁癖：[refresh] 是被无障碍
 * 事件回调调用的，而那个回调跑在**主线程**（`ActivityThread` 的 Looper），重算却要做同步
 * IPC——两者相加就把主线程按住了。真机上 `/data/anr/traces.txt` 里主线程栈正是
 * `A11yService.onAccessibilityEvent → BathAppPage.refresh → A11yProbe.find`，
 * 而 `WatchdogReceiver` 的广播排在主线程后面等满 10 秒，系统随即以 `background ANR`
 * 把整个进程杀掉，表现为网关每两分钟断一次。主线程只做一次入队。
 *
 * [snapshot] 与订阅回调可能来自任意线程，因此状态用 `@Volatile`、订阅表用
 * `CopyOnWriteArrayList`。
 */
object BathAppPage {

    /**
     * 请求重算的信号；容量 1，满了就是"已经有一次待办"。
     *
     * 用队列而不是 `wait/notify` 或"提交一个任务"，是为了同时满足两件事：**不重复算**
     * （多次变化合并成一次重算）与**不漏算最后一个**（线程忙于重算时来的请求会留在队列里，
     * 它一空闲就会立刻取出再算一次）。
     */
    private val signal = ArrayBlockingQueue<Unit>(1)

    /** 是否已有一次待办的重算。 */
    private val pending = AtomicBoolean(false)

    @Volatile
    private var cached = PageSnapshot(PageState.UNKNOWN)

    private val listeners = CopyOnWriteArrayList<(PageSnapshot) -> Unit>()

    init {
        Thread(::loop, "page-refresh").apply { isDaemon = true }.start()
    }

    /** 当前页面状态。纯内存读，任意线程可用。 */
    fun snapshot(): PageSnapshot = cached

    /**
     * 请求一次重算；**立即返回**，实际重算在 [loop] 那个线程上做。
     *
     * 由无障碍服务的事件回调调用（主线程），所以这里只允许做原子操作与一次入队——[event]
     * 的统计同样受这条约束，它只是往一张内存表里加个计数。
     *
     * 不值得算的事件（判据见 [PageEventRules]）**只进统计、不入队**：这正是它省下来的那部分。
     * 统计不能一起省——被筛掉的条数本身就是"筛掉了多少"这个答案。
     *
     * [event] 为 null 表示这是主动请求（自动化流程动手前先要一份当前状态），一律照算。
     */
    internal fun refresh(event: ContentEvent? = null) {
        if (event != null) {
            AutomationTrace.recordEvent(event)
            if (!PageEventRules.mayChangePage(event)) return
        }
        if (pending.getAndSet(true)) return
        signal.offer(Unit)
    }

    /** 重算线程的主循环：取信号 → 重算 → 放行等待者。 */
    private fun loop() {
        while (true) {
            try {
                signal.take()
            } catch (_: InterruptedException) {
                return
            }
            pending.set(false)
            recompute()
            A11yService.onPageRefreshed()
        }
    }

    private fun recompute() {
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
     * 回调在重算线程（`page-refresh`）上执行——**不要在里面做长操作**，那会拖住后续的重算。
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
 * 探针的真实现：取回活动窗口的树，在本地走一遍建成资源 ID 到文本的表。
 *
 * ## 为什么在本地遍历，而不是按 ID 交给系统查
 *
 * `findAccessibilityNodeInfosByViewId` 看着更省——一次调用、由系统在服务侧做完。真机上量
 * 出来是反的：
 *
 * | 做法 | 取根节点 | 取特征 | 合计 |
 * |---|---|---|---|
 * | 本地遍历 | 35ms | ~6ms | **约 42ms** |
 * | 按 ID 查 | 35ms | 26ms | **约 61ms** |
 *
 * `rootInActiveWindow` 已经把整棵树取回本地（实测 35ms，与树多大无关），此后 52 次
 * `getChild` 是从这份**本地**缓存里取；而按 ID 查是**另一次系统调用**，系统要在服务侧重走
 * 一遍树并构建命中节点的列表——平均 26ms，最长的一次到过 386ms。每次界面变化都要走一遍，
 * 这一进一出就是三分之一核的差别。
 *
 * 遍历只在**第一次 [find] 时**发生，之后都查表：判定规则是懒的，稳态下只问第一句，走一遍
 * 就够；页面切换时可能问到第六句，那时表已经建好了。
 *
 * 这段耗时单独报给 [AutomationTrace]（`qavg`），与总耗时（`avg`）对照就能看出"取根节点"
 * 与"取特征"各占多少——两者可优化的手段完全不同。
 */
private class A11yProbe(private val root: AccessibilityNodeInfo) : PageProbe {

    private var index: Map<String, String>? = null

    override fun find(resId: String): PageElement? = table()[resId]?.let { PageElement(it) }

    /**
     * 建好的表；没有就建一次。
     *
     * 表里放的是**所有带资源 ID 的控件**，不只判定规则关心的那几个——探针不该知道规则关心
     * 什么，而多收十来个条目的代价远小于多走一遍树。
     */
    private fun table(): Map<String, String> {
        index?.let { return it }
        val startedAt = System.nanoTime()
        val built = HashMap<String, String>()
        try {
            collect(root, built)
        } finally {
            AutomationTrace.recordQueryTime((System.nanoTime() - startedAt) / 1_000_000)
        }
        index = built
        return built
    }

    /** 一次 DFS 收表；同名控件只留最靠前的那个（判定只认第一个）。 */
    private fun collect(node: AccessibilityNodeInfo, table: MutableMap<String, String>) {
        val rid = node.viewIdResourceName
        // 文本与资源 ID 都是节点上的本地字段，不产生额外 IPC
        if (rid != null && !table.containsKey(rid)) table[rid] = node.text?.toString() ?: ""
        for (position in 0 until node.childCount) {
            val child = node.getChild(position) ?: continue
            try {
                collect(child, table)
            } finally {
                child.recycle()
            }
        }
    }
}
