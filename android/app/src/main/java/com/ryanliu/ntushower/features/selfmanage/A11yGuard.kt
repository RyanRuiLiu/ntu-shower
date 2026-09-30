package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.shell.RootShell
import com.ryanliu.ntushower.service.A11yService

/**
 * 无障碍链路守护：核对总开关与服务清单，不对就修。
 *
 * ## 为什么不能只保证"清单里有本应用"
 *
 * 无障碍由**两个**设置共同决定：总开关 `accessibility_enabled` 与服务清单
 * `enabled_accessibility_services`。只核对清单会漏掉总开关被关的情况——真机上
 * 发生过：清单完好、总开关为 0，于是设备上**所有**无障碍服务一起失效；本应用
 * 失去无障碍这个自举锚点（`onServiceConnected` 里拉起主服务），同机的旧系统
 * 一并瘫痪。此前的实现只查清单，故障期间每次都判定"正常"而放行，一次都没修。
 *
 * 这两项之外还有**第三个**前提：系统手上那份"已安装无障碍服务"的表里得有本组件。
 * 那是 `PackageManager` 的查询结果，由包变更事件驱动刷新，本身也会过期——OTA 换包
 * 之后系统走了卸载路径，把本组件从表里摘掉却没加回，此时前两项怎么改都不会生效。
 * 那条恢复路径见 [requestRescan]。
 *
 * ## 总开关被回写时的可用路径
 *
 * 总开关为 0 时 `settings put secure accessibility_enabled 1` 会被系统**立即回写
 * 为 0**（紧接写入读取仍是 0，毫秒级），`content insert` 同样无效；对照组的自建
 * key 写入正常，说明写入路径本身没坏，是这个开关的变更被拒。
 *
 * 可用路径分两步：先**绕过变更通知直接写存储**，再制造一次 0→1 跳变。跳变不是
 * 多余的——直写只改了存储，系统内存里的开关状态不会因此刷新，只有一次真实的
 * 0→1 变化才会让它重新加载配置并绑定服务。真机验证：按此顺序执行后总开关稳定为
 * 1，服务重新绑定，界面操作恢复。
 *
 * 旧系统的同类守护每 60 秒用 `settings put` 重试一次，同样写不进去——只重试
 * 标准接口不足以恢复。
 */
object A11yGuard {

    private const val TAG = "A11yGuard"

    /** 无障碍设置在系统数据库中的位置：标准接口被拒时的替代写入目标。 */
    private const val SETTINGS_DB = "/data/data/com.android.providers.settings/databases/settings.db"

    /** 把总开关写成开启的 SQL。`settings put` 被系统回写时改用它直写存储。 */
    private const val SQL_SET_ENABLED = "update secure set value='1' where name='accessibility_enabled'"

    /** 摘除与放回之间留的间隔（秒）：太短会被系统合并成同一次评估，重绑也就不会发生。 */
    private const val REBIND_GAP_S = 1

    private const val APP_PACKAGE = "com.ryanliu.ntushower"
    private const val COMPONENT = "$APP_PACKAGE/.service.A11yService"

    /** 修完之后等系统重绑的上限。 */
    private const val BIND_WAIT_MS = 10_000L

    /** 等待重绑时的轮询间隔。 */
    private const val BIND_POLL_MS = 250L

    /** 核对并按需修复；返回修复后链路是否完整。链路本已完整时是只读操作。 */
    fun ensureAvailable(): Boolean {
        val bound = A11yService.instance != null
        val switch = readSwitch()
        val list = readServiceList()
        if (!A11yState.needsRepair(switch, list, COMPONENT, bound)) return true

        Log.w(TAG, "accessibility degraded: bound=$bound enabled=$switch, repairing")
        if (!bound && !A11yState.needsRepair(switch, list, COMPONENT, bound = true)) {
            // 设置全对却没绑上：系统侧的绑定状态坏了，改值没用（写同样的值它不重新评估），
            // 必须把组件从清单里摘掉再放回，逼它重走一遍绑定
            rebind()
        }
        if (!A11yState.isSwitchOn(switch)) enableSwitch()
        if (!A11ySettingsMerge.contains(readServiceList(), COMPONENT)) appendToServiceList()

        var stillBound = waitForBind()
        if (!stillBound) {
            // 设置层全对却仍不绑：系统缓存的那份"已安装无障碍服务"过期了。真机上出过——
            // OTA 换包时系统走了卸载路径（日志里写着 `Force stopping ... : uninstall pkg`），
            // 把本组件从那张表里摘掉却没有加回。此后 settings 怎么写它都不认，
            // `dumpsys accessibility` 里根本没有这个服务，只修设置是修不好的。
            requestRescan()
            stillBound = waitForBind()
        }
        if (stillBound) {
            Log.i(TAG, "accessibility restored")
        } else {
            Log.e(TAG, "accessibility repair failed")
        }
        return stillBound
    }

    /**
     * 广播一次包变更，让系统重新扫描已安装的无障碍服务。
     *
     * 这是设置层之外的另一条恢复路径，两者管的不是同一件事：`settings` 决定"系统该不该启用
     * 它"，而那张已安装表决定"系统知不知道有这个组件"。表过期时，设置写得再对也不会触发
     * 绑定——真机上表现为 `enabled_accessibility_services` 内容完好、总开关为 1，服务却迟迟
     * 不绑，而 `dumpsys accessibility` 的服务清单里压根没有本应用。
     *
     * **脱手发**，不等结果：这条广播会让系统重扫所有包，耗时从几秒到几十秒不等，占住常驻
     * 通道会把后面那几条真正把开关写对的 `settings` 一起拖住。广播是否成功本身也不重要，
     * 要的是它触发的重扫，而那是异步发生的。
     */
    private fun requestRescan() {
        RootShell.execDetached("am broadcast -a android.intent.action.PACKAGE_CHANGED -d package:$APP_PACKAGE")
    }

    /**
     * 等系统把服务绑回来，最多 [BIND_WAIT_MS]。
     *
     * `settings put` 之后系统的重绑是**异步**的，几百毫秒到数秒不等。写完就立刻读
     * `A11yService.instance` 多半还是 null，于是刚刚修好的那一次被记成
     * `accessibility repair failed`——排障时把人往完全相反的方向带。
     *
     * 只做一轮等待，不做多层重试环：看门狗本身就是重试入口，60 秒后还会再来一次。
     * 这个方法只在修复路径上调用（链路本来就完整时提前返回了），正常运行时不会走到。
     */
    private fun waitForBind(): Boolean {
        val deadline = System.currentTimeMillis() + BIND_WAIT_MS
        while (System.currentTimeMillis() < deadline) {
            if (A11yService.instance != null) return true
            Thread.sleep(BIND_POLL_MS)
        }
        return A11yService.instance != null
    }

    /**
     * 把本组件从服务清单里摘掉再放回，逼系统重绑。
     *
     * 只对"设置正确但服务没绑上"这一种故障有用——那种状态下改任何值都不触发重新评估，
     * 因为值本来就没变。真机上靠这条把整片失效的界面能力救了回来。
     */
    private fun rebind() {
        val full = readServiceList()
        val without = full.split(':').filter { it.isNotBlank() && !it.contains(APP_PACKAGE) }.joinToString(":")
        RootShell.control.exec("settings put secure enabled_accessibility_services \"$without\"")
        // 给系统一点时间处理上一次变更，否则两次写入可能被合并成同一次评估
        RootShell.control.exec("sleep $REBIND_GAP_S; settings put secure enabled_accessibility_services \"$full\"")
    }

    private fun readSwitch(): String = RootShell.control.exec("settings get secure accessibility_enabled").output.trim()

    private fun readServiceList(): String =
        RootShell.control.exec("settings get secure enabled_accessibility_services").output.trim()

    /** 把本应用并入服务清单（append 语义，不覆盖设备上已有的其他无障碍服务）。 */
    private fun appendToServiceList() {
        val merged = A11ySettingsMerge.mergeSettingsValue(readServiceList(), COMPONENT)
        RootShell.control.exec("settings put secure enabled_accessibility_services \"$merged\"")
    }

    /** 打开总开关：先走标准接口，被回写时改走存储直写加一次跳变（理由见类注释）。 */
    private fun enableSwitch() {
        RootShell.control.exec("settings put secure accessibility_enabled 1")
        if (A11yState.isSwitchOn(readSwitch())) return

        RootShell.control.exec("sqlite3 $SETTINGS_DB \"$SQL_SET_ENABLED\"")
        RootShell.control.exec("settings put secure accessibility_enabled 0")
        RootShell.control.exec("settings put secure accessibility_enabled 1")
    }
}
