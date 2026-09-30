package com.ryanliu.ntushower.features.automation

/**
 * 浴室 App 崩溃守护（纯逻辑，可单测）：
 * 浴室 App 退出到桌面的事件流经此决策——是否拉起、是否进入冷却。
 *
 * 对齐旧系统真机验证过的语义（ADR-0024 参数化后移植）：
 * - [windowMs] 内退出达 [maxCount] 次 → 进入 [cooldownMs] 冷却期，冷却期内不拉起；
 * - 浴室 App 回到前台即解除冷却；
 * - 自动化执行期间不拉起（由调用方判断 automationInProgress）。
 */
class BathAppGuard(private val windowMs: Long, private val maxCount: Int, private val cooldownMs: Long) {

    enum class Action {
        /** 无动作（次数未达阈值）。 */
        NONE,

        /** 拉起浴室 App。 */
        RELAUNCH,

        /** 达到阈值，进入冷却（本次不拉起）。 */
        COOLDOWN,
    }

    private val exitHistory = ArrayDeque<Long>()
    private var cooldownUntil = 0L

    /** 浴室 App 回到前台：清除冷却与历史。 */
    fun onForeground() {
        cooldownUntil = 0L
        exitHistory.clear()
    }

    /** 浴室 App 退出到桌面：返回本次应执行的动作。 */
    fun onBackground(nowMs: Long): Action {
        if (nowMs < cooldownUntil) return Action.COOLDOWN
        exitHistory.addLast(nowMs)
        val cutoff = nowMs - windowMs
        while (exitHistory.isNotEmpty() && exitHistory.first() < cutoff) {
            exitHistory.removeFirst()
        }
        return if (exitHistory.size >= maxCount) {
            cooldownUntil = nowMs + cooldownMs
            exitHistory.clear()
            Action.COOLDOWN
        } else {
            Action.RELAUNCH
        }
    }

    /** 当前是否处于冷却期。 */
    fun isCoolingDown(nowMs: Long): Boolean = nowMs < cooldownUntil
}
