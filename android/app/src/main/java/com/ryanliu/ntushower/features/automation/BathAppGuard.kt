package com.ryanliu.ntushower.features.automation

/**
 * 浴室 App 崩溃守护（纯逻辑，可单测）：
 * 浴室 App 退出到**桌面**的事件流经此决策——是否拉起、是否进入冷却。
 *
 * 对齐旧系统真机验证过的语义（docs/adr/ 参数化后移植）：
 * - [onBackground] 的 [Params.windowMs] 内退出达 [Params.maxCount] 次 → 进入冷却期，
 *   冷却期内不拉起；
 * - 浴室 App 回到前台即解除冷却；
 * - 自动化执行期间不拉起（由调用方判断 automationInProgress）。
 *
 * **只对「退到桌面」计数**（见 [onBackground] 的 `leftToPackage`）：退出到别的包并不是
 * "有人想离开浴室界面"，而是现场人员走调试通道（设备断网时远程失效、又插不了线，那条
 * 通道是刻意留的）。那种退出既不拉起、也不记账——记账会让走通道的人把阈值打满，真需要
 * 拉起时反而进了冷却。桌面包集合由调用方给，本类不认识具体包名。
 *
 * 阈值由调用方**每次调用时传入**而不是构造期固化：它们是远程配置项，改成构造参数的话，
 * 服务端改了配置还要等本服务重绑才生效——而重绑恰恰是故障场景里最不可得的。
 * 状态（退出历史、冷却截止）仍是它自己的：那是决策的依据，不是配置。
 */
class BathAppGuard {

    /** 一次判定的阈值集合。取值来自 [com.ryanliu.ntushower.core.config.RuntimeConfig]（唯一来源）。 */
    data class Params(val windowMs: Long, val maxCount: Int, val cooldownMs: Long)

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

    /**
     * 浴室 App 退到 [leftToPackage]：返回本次应执行的动作。
     *
     * 去向不在 [desktopPackages] 里时直接给 [Action.NONE]，**不动退出历史、也不进冷却**：
     * 那不是"人离开了浴室界面"，而是现场人员走调试通道。
     */
    fun onBackground(nowMs: Long, leftToPackage: String, desktopPackages: Set<String>, params: Params): Action {
        if (leftToPackage !in desktopPackages) return Action.NONE
        if (nowMs < cooldownUntil) return Action.COOLDOWN
        exitHistory.addLast(nowMs)
        val cutoff = nowMs - params.windowMs
        while (exitHistory.isNotEmpty() && exitHistory.first() < cutoff) {
            exitHistory.removeFirst()
        }
        return if (exitHistory.size >= params.maxCount) {
            cooldownUntil = nowMs + params.cooldownMs
            exitHistory.clear()
            Action.COOLDOWN
        } else {
            Action.RELAUNCH
        }
    }

    /** 当前是否处于冷却期。 */
    fun isCoolingDown(nowMs: Long): Boolean = nowMs < cooldownUntil
}
