package com.ryanliu.ntushower.features.selfmanage

/**
 * 无障碍服务清单的合并（纯逻辑，可单测）。
 *
 * 目标：确保本应用在列，同时**不覆盖**原有条目（设备上可能同时启用其他
 * 无障碍服务，覆盖掉等于把别人的功能关掉）。
 */
object A11ySettingsMerge {

    /** 系统设置里表示"空"的几种取值。 */
    private val EMPTY_VALUES = setOf("", "null", "none")

    /**
     * 把 [component] 并入现有清单。
     *
     * 已在列则原样返回；否则追加（原有条目保持不变）。
     */
    fun mergeSettingsValue(current: String, component: String): String {
        if (current.contains(component)) return current
        return if (current.trim().lowercase() in EMPTY_VALUES) component else "$current:$component"
    }

    /** 清单里是否已包含该组件。 */
    fun contains(current: String, component: String): Boolean = current.contains(component)
}
