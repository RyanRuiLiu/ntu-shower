package com.ryanliu.ntushower.features.selfmanage

/**
 * 无障碍链路的期望状态判定（纯逻辑，可单测）。
 *
 * 链路完整需要**三个**条件同时成立，缺一即失效（为什么要一起看，见 [A11yGuard]）：
 * 服务真的绑上了、总开关为 `1`、服务清单包含本应用。
 */
object A11yState {

    /** 系统设置里表示"已开启"的取值。 */
    const val ENABLED = "1"

    /**
     * 总开关读数是否已开启。
     *
     * 读数来自 `settings get`，末尾可能带换行；`null`、空串这类异常取值一律按
     * "未开启"处理——判错方向的代价是整机无障碍失效，而不是多写一次设置。
     */
    fun isSwitchOn(raw: String): Boolean = raw.trim() == ENABLED

    /**
     * 链路是否需要修复。
     *
     * `bound` 是键——只查设置会漏掉最隐蔽的一种故障：开关与清单都正确，系统侧的
     * 绑定状态却坏了，服务压根没连上。真机上发生过：进程被杀后系统没有重绑
     * `A11yService`，界面能力整片失效，而设置查起来一切都是对的，于是守护每次都
     * 判定"正常"而放行。
     *
     * `component` 必须是**完整组件名**（`包名/.service.A11yService`），不是裸包名。
     * 清单判定是子串匹配，拿裸包名去比会命中形如 `com.ryanliu.ntushower` 的残缺条目
     * ——系统并不认识那种写法，服务没绑上，而这里会判定"已在列"并放行。
     */
    fun needsRepair(switchRaw: String, serviceList: String, component: String, bound: Boolean): Boolean =
        !bound || !isSwitchOn(switchRaw) || !A11ySettingsMerge.contains(serviceList, component)
}
