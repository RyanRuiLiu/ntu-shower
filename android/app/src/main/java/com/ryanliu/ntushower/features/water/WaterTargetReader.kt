package com.ryanliu.ntushower.features.water

import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.core.shell.ShellChannel

/**
 * 从浴室 App 的 `data.xml` 读取 waterList 并换算目标；设备 IO 外壳。
 *
 * 与 [WaterTargetResolver] 分开：后者是纯解析/区间换算，可在 JVM 里测；这里只做
 * `cat` 与错误分支。`read` 参数供单测注入固定输出，生产默认走 root shell。
 */
object WaterTargetReader {

    private const val TAG = "WaterTargetReader"

    /** 每次动作现读 `data.xml`，不缓存：App 会在 V8 参数下发后改写这份文件。 */
    fun boards(
        read: (String) -> ShellChannel.Result = { RootShell.control.exec(it) },
    ): List<WaterTargetResolver.Board> {
        val result = read("cat ${AppConfig.BATH_PREFS_PATH}")
        if (result.exitCode != 0 || result.output.isBlank()) {
            Log.w(TAG, "读 data.xml 失败，exit=${result.exitCode}")
            return emptyList()
        }
        return WaterTargetResolver.parse(result.output)
    }

    /** 逻辑门号 → 板子与板内门号；不在任何 waterList 区间时返回 null。 */
    fun target(
        doorNo: Int,
        read: (String) -> ShellChannel.Result = { RootShell.control.exec(it) },
    ): WaterTargetResolver.Target? = WaterTargetResolver.targetIn(boards(read), doorNo)
}
