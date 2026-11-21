package com.ryanliu.ntushower.core.shell

import android.util.Log

/**
 * root shell 门户：按用途把命令路由到不同的常驻通道，或独立通道。
 *
 * ## 为什么不是一条 shell
 *
 * 设备的 root 通道有三类用途，**打扰预算**各不相同。共用一条常驻 shell 会让它们
 * 互相排队——命令在通道内由 `@Synchronized` 串行，一条 20 秒的远程命令就能让门
 * 状态晚 20 秒上报，而用户正看着那份状态挑喷头。
 *
 * | 通道 | 进程 | 承载 | 判据 |
 * |---|---|---|---|
 * | [data] | 常驻，独占 | 读浴室 APP 的数据库 | **生命线**：数据库一变就触发，延迟直接让用户选错喷头 |
 * | [control] | 常驻 | 界面注入、设置读写、adbd | 都是短命令，彼此短暂排队可容忍 |
 * | [command] | 常驻，独占 | 远程任意命令 | 内容不可预期，不能占住控制通道的串行锁 |
 * | [execDetached] | 每次独立，不等结果 | OTA 安装、重启整机、重启本程序 | 必须活过本进程 |
 *
 * 判据只有两条：**它是生命线吗**；**它会活过本进程吗**。都否，进 [control]。
 */
object RootShell {

    /**
     * 数据通道：读浴室 APP 的数据库。
     *
     * **独占**——其他任何用途都不得借用。它是本项目的生命线，与无关命令共用进程
     * 会让喷头状态上报被拖住，而用户正拿这份状态挑喷头。
     */
    val data: ShellChannel = ShellChannel("data")

    /**
     * 控制通道：界面注入、设置读写、adbd 启停这类短命令。
     *
     * 它们都短、都低频，彼此短暂排队可以容忍，不值得各占一个进程。
     */
    val control: ShellChannel = ShellChannel("control")

    /**
     * 远程命令通道：承载 `device.exec` 下发的任意命令。
     *
     * 与 [control] 分开而不是共用，是因为它的命令内容**不可预期**——一条 `sleep 20`
     * 挂在控制通道上，界面注入与无障碍守护就得陪着等 20 秒。分开之后，慢命令只影响
     * 它自己那条通道。
     *
     * 它的 [ShellChannel.maxOutputBytes] 固定取 `AgentService` 的
     * DEVICE_EXEC_MAX_OUTPUT_BYTES，配置同步时写入。
     */
    val command: ShellChannel = ShellChannel("command")

    /**
     * 脱手执行：起一个独立 root 进程，不等结果。
     *
     * 只用在**必须活过本进程**的场景（`pm install` 会为了替换 APK 杀掉本进程；
     * 重启整机与本程序同理）。需要输出的命令走 [command]。
     *
     * 用 `su 0 sh -c` 把整条脚本交给一个 shell 解析：设备上的 `su` 本身不吃 `-c`
     * （`su -c "echo x"` 会把命令当用户名，报 `Unknown id`），所以必须显式给出要跑的
     * shell。这条路径在 OTA 安装上反复验证过。
     */
    fun execDetached(command: String): Boolean = try {
        val process = Runtime.getRuntime().exec(arrayOf("su", "0", "sh", "-c", command))
        drain(process)
        true
    } catch (e: Exception) {
        Log.e(TAG, "detached exec failed: ${e.message}")
        false
    }

    /**
     * 排空子进程的输出流。
     *
     * 必须做：管道缓冲区满会让子进程阻塞在 write 上，脚本就停在那里不动了。
     * 顺带 `waitFor` 收割进程，避免留下已退出但未回收的僵尸。
     */
    private fun drain(process: Process) {
        Thread({
            try {
                process.inputStream.use { it.readBytes() }
                process.errorStream.use { it.readBytes() }
                process.waitFor()
            } catch (_: Exception) {
                // 读取输出失败不影响脱手进程本身继续执行
            }
        }, "detached-shell-drain").apply { isDaemon = true }.start()
    }

    /**
     * 清扫被异常打断的命令留下的 pid 文件（见 [ShellChannel.wrap]：正常路径由包裹
     * 脚本自删，进程被杀、断电那种时刻没人替它收尾）。
     *
     * 只在服务启动时调：此刻没有任何命令在跑，/data/local/tmp 里属于本程序的 pid
     * 文件全部是死的。文件归 root 所有，走 control 通道删——这套命名是 shell 通道
     * 自己的约定，清扫也归它。首次拉起时 control 通道顺带完成 su 授权。
     */
    fun cleanupAbandonedPidFiles() {
        control.exec("rm -f /data/local/tmp/ntushower-*.pid")
    }

    /** 销毁三条常驻通道。下次使用时各自重新拉起。 */
    fun destroy() {
        data.destroy()
        control.destroy()
        command.destroy()
    }

    private const val TAG = "RootShell"
}
