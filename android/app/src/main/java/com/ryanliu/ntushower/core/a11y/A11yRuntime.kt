package com.ryanliu.ntushower.core.a11y

import com.ryanliu.ntushower.core.clock.Uptime
import java.util.concurrent.atomic.AtomicLong

/**
 * 无障碍链路的进程内锚点：绑定状态、前台标志、自动化互斥标志与页面变化信令的唯一存放处。
 *
 * 这些状态的使用者横跨 features 与 service。原先存放在 A11yService 的伴生对象里，迫使
 * 每个使用者 import 服务类，与「AgentService 装配 features」合成 service ↔ features 的
 * 包级循环；抽到 core 之后方向恢复单向：service 写入（绑定、窗口事件），features 读取
 * 与等待。
 *
 * [bathAppInForeground] 只由 A11yService 的窗口状态事件维护；[automationInProgress] 只由
 * 自动化调度置位、由 A11yService.onDestroy 复位。其余模块只读。
 */
object A11yRuntime {

    /** 当前绑定的无障碍服务；未绑定或已解绑时为 null。 */
    @Volatile
    var source: A11yNodeSource? = null
        private set

    /** 浴室 App 是否在前台。 */
    @Volatile
    var bathAppInForeground = false

    /** 自动化流程执行中标志（崩溃守护在此期间不拉起）。 */
    @Volatile
    var automationInProgress = false

    /** 服务绑定时注册（A11yService.onServiceConnected 调用）。 */
    fun bind(service: A11yNodeSource) {
        source = service
    }

    /** 服务销毁时解绑（A11yService.onDestroy 调用）。 */
    fun unbind() {
        source = null
    }

    private val pageChangeLock = Object()

    /** 已完成的重算次数：配合 [pageChangeMark] / [awaitPageChangeAfter] 用。 */
    private val pageChangeCount = AtomicLong(0)

    /**
     * 一次页面重算已完成（由 BathAppPage 的重算线程调用）：放行正在等页面变化的调用方。
     *
     * 由重算线程调用而不是由事件回调调用，是这次线程模型调整的关键：事件回调跑在主线程，
     * 那里不能做重算要的同步 IPC；而「放行等待者」必须发生在**状态已经跟上之后**，等待方
     * 被唤醒就会立刻读 BathAppPage.snapshot，反过来就是它醒来看见旧值、再空转一轮。
     *
     * 多次界面变化可能合并成一次重算，所以这个计数不再等于「内容变化次数」，而是「重算
     * 次数」——对 [awaitPageChangeAfter] 的用法（只比「是否大于动手前那个 mark」）没有影响。
     */
    fun onPageRefreshed() {
        pageChangeCount.incrementAndGet()
        synchronized(pageChangeLock) { pageChangeLock.notifyAll() }
    }

    /** 取当前的变化计数。**在动手之前取**——之后拿它判断「这一下有没有让页面变」。 */
    fun pageChangeMark(): Long = pageChangeCount.get()

    /**
     * 等到页面内容在 [mark] 之后发生变化，最多等 [timeoutMs]（事件驱动，无人等待时零开销）。
     *
     * 为什么要传一个 mark，而不是单纯 `wait`：`Object.wait` 等的是**下一次**通知，
     * 通知若发生在调用之前就永远等不到，只能等满上限。而「点完按钮页面立刻就变了」恰恰
     * 是最常见的情形——固定间隔的等待全数落空，一个流程里要发生五六次。
     *
     * 拿到锁之后再比一次，是为了盖住「取 mark 到进锁之间」那一小段；`wait` 被无关通知
     * 唤醒时也会绕回循环再比。
     */
    fun awaitPageChangeAfter(mark: Long, timeoutMs: Long) {
        synchronized(pageChangeLock) {
            val deadline = Uptime.nowMs() + timeoutMs
            while (pageChangeCount.get() == mark) {
                val left = deadline - Uptime.nowMs()
                if (left <= 0) return
                pageChangeLock.wait(left)
            }
        }
    }

    /** 阻塞等待下一次页面变化或超时；不关心「此前是否已经变过」的场景用它。 */
    fun awaitPageChange(timeoutMs: Long) {
        synchronized(pageChangeLock) { pageChangeLock.wait(timeoutMs) }
    }
}
