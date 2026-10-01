package com.ryanliu.ntushower.core.shell

import android.util.Log
import java.io.BufferedReader
import java.io.DataOutputStream
import java.io.InputStreamReader
import java.util.UUID
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/**
 * 一条常驻 root shell 通道。
 *
 * 进程只 spawn 一次，命令经 stdin 持续下发，以标记行分隔输出（旧系统真机验证
 * 过的模式）。为什么不用"每次命令独立 spawn"：测量显示 spawn 成本占主导
 * （~100ms/次）；常驻进程后单命令降为毫秒级。
 *
 * ## 一条通道 = 一个串行队列
 *
 * 命令由 `@Synchronized` 串行化，**同一条通道上的命令必然互相排队**。所以通道
 * 的划分依据是用途而不是性能：把读数据库和界面注入放进同一条，一条慢命令就会
 * 让喷头状态晚上报同样长的时间。该分几条、按什么分，见 [RootShell] 的类注释。
 *
 * 超时是必须的：`readLine()` 在命令不返回时会永久阻塞，而这里是串行的——一条
 * 挂住的命令（等输入、卡在内核）会冻结本通道的全部调用，进而拖死它的使用者。
 *
 * ## 但超时只让我们不再等它，不会让它停
 *
 * 真机上因此留下过 99% CPU 的孤儿：超时后 `process.destroy()` 只掀掉 Java 直接启动的那
 * 一层，真正在跑的 `sh` 被 init 收养、继续跑完。取舍与做法见 [wrap] 与
 * [terminateRunningCommand]。
 */
class ShellChannel(private val name: String) {

    /**
     * 单条命令的最大累积输出（字节），**运行时可调**。
     *
     * 由配置项 `device_exec_max_output_bytes` 驱动（见 [RootShell.command]）：上限必须
     * 可调，写死的数字换个场景就不合适了。默认不限制，因为常驻通道的命令都是我们自己
     * 写的短命令；只有承载"内容不可预期"的通道才需要设它。
     *
     * 它在**读取时**生效，不是读完再截——一条 `dumpsys` 的输出被整块读进内存，足以让
     * agent 进程被系统杀掉、设备从此失联。
     */
    @Volatile
    var maxOutputBytes: Int = Int.MAX_VALUE

    /**
     * 一次 shell 调用的结果。
     *
     * 成没成**只看 [exitCode]**，没有另设一个布尔字段：两个字段表达同一件事，就有各说各话
     * 的机会。读不到退出码时取值是 [ShellOutput.UNKNOWN_EXIT_CODE]，与真实的 0 分得开。
     *
     * [timedOut] 从失败里单独拎出来，是为了让上层能回报协议的 `timeout` 状态而不是笼统的
     * `failed`——它俩对客户端的意义不同：超时表示"不知道成没成"，失败表示"有结论，结论是
     * 没成"。
     */
    data class Result(
        val output: String,
        val exitCode: Int = ShellOutput.UNKNOWN_EXIT_CODE,
        val timedOut: Boolean = false,
    )

    private var process: Process? = null
    private var stdin: DataOutputStream? = null
    private var stdout: BufferedReader? = null

    /**
     * 拉起常驻进程；超过 [SHELL_START_TIMEOUT_MS] 没起来就算失败。
     *
     * 上限不可省：`Runtime.exec` 本身不可中断，ROM 的 `su` 一旦在启动阶段挂住就再也不会
     * 返回。真机上出过——本通道的 `@Synchronized` 被那条永不返回的调用一直持有，而调用它
     * 的执行器是单线程的，于是排在该执行器上的每条命令都无声消失：服务端只看到一串超时，
     * 设备侧连"我超时了"都回报不出来（回报本身要经同一个执行器）。
     */
    @Synchronized
    private fun ensureShell(): Boolean {
        process?.let { p ->
            try {
                p.exitValue()
            } catch (e: IllegalThreadStateException) {
                return true // 仍存活
            }
            process = null
        }
        val task = FutureTask { startShell() }
        Thread(task, "root-shell-start").apply {
            isDaemon = true
            start()
        }
        return try {
            task.get(SHELL_START_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (e: Exception) {
            // 超时意味着那个启动线程多半还挂在 exec 里，回收不掉，会泄漏一个线程。这是有意
            // 的取舍：宁可漏一个线程，也不能让本通道把调用方的执行器一起拖死。
            Log.w(TAG, "[$name] su did not start within ${SHELL_START_TIMEOUT_MS}ms: ${e.message}")
            resetShell()
            false
        }
    }

    /** 真正拉起进程；放在 [ensureShell] 的带超时任务里跑，所以这里不必自己设上限。 */
    private fun startShell(): Boolean = try {
        val started = Runtime.getRuntime().exec("su")
        process = started
        stdin = DataOutputStream(started.outputStream)
        // 两侧都显式指定 UTF-8。不能依赖平台默认编码：命令可能含中文（远程命令是我们
        // 自己发下去的，内容不可预期），而 su 链路上的字节是按 UTF-8 交换的。
        stdout = BufferedReader(InputStreamReader(started.inputStream, Charsets.UTF_8))
        Thread {
            try {
                started.errorStream.readBytes() // 丢弃 stderr，防止缓冲区塞满
            } catch (_: Exception) {
                // stderr 读取失败不影响命令执行
            }
        }.start()
        true
    } catch (e: Exception) {
        Log.e(TAG, "[$name] failed to start su: ${e.message}")
        false
    }

    @Synchronized
    fun exec(command: String, timeoutMs: Long = DEFAULT_TIMEOUT_MS): Result {
        if (!ensureShell()) {
            return Result("root shell unavailable")
        }
        val stamp = System.nanoTime()
        val marker = "__DONE_${stamp}_${UUID.randomUUID().toString().take(8)}__"
        val pidFile = "/data/local/tmp/ntushower-$name-$stamp.pid"
        return try {
            // 用 write(ByteArray) 而不是 writeBytes(String)。
            //
            // `writeBytes` 的语义是"逐字符写出并**丢弃高 8 位**"，中文命令到了设备就变成
            // 一串 ASCII 噪声。真机上验证过：`echo 测试` 实际执行的是 `echo K-`——因为
            // 「测」是 U+6D4B 而「试」是 U+8BD5，低 8 位正是 0x4B 与 0xD5 里那截。
            stdin!!.write(wrap(command, marker, pidFile).toByteArray(Charsets.UTF_8))
            stdin!!.flush()
            when (val captured = readUntil(marker, timeoutMs, pidFile)) {
                null -> Result("命令超过 ${timeoutMs}ms 未返回", timedOut = true)
                else -> Result(captured.output, captured.exitCode)
            }
        } catch (e: Exception) {
            // 管道断裂：销毁进程，下次调用重新拉起
            resetShell()
            Result(e.message ?: "shell broken")
        }
    }

    /**
     * 读到标记为止；超时或读取异常返回 null。
     *
     * 超时路径做两件事，缺一不可：**终止那条命令**（[terminateRunningCommand]）——我们
     * "不再等它"不等于"它停了"；**销毁进程**——读线程正阻塞在 readLine 上，只有关闭管道
     * 才能让它退出（否则每次超时都泄漏一个阻塞线程）。
     */
    private fun readUntil(marker: String, timeoutMs: Long, pidFile: String): ShellOutput.Captured? {
        val reader = stdout ?: return null
        val task = FutureTask {
            ShellOutput.collect(reader.lineSequence(), marker, maxOutputBytes)
        }
        Thread(task, "root-shell-read").apply {
            isDaemon = true
            start()
        }
        return try {
            task.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (e: TimeoutException) {
            Log.w(TAG, "[$name] command timed out after ${timeoutMs}ms, terminating it and recycling shell")
            terminateRunningCommand(pidFile)
            resetShell()
            null
        } catch (e: Exception) {
            Log.w(TAG, "[$name] read failed: ${e.message}, recycling shell")
            resetShell()
            null
        }
    }

    /**
     * 把一条命令包成可终止的形式。
     *
     * 不包的话，超时之后那条命令**仍在设备上跑**：[resetShell] 的 `process.destroy()`
     * 只作用于 Java 直接启动的那个进程，而设备上的真实进程链是 `su → su → su → sh`
     * （真机 `ps` 实测），它只掀掉最上面一层，执行命令的 `sh` 被 init 收养后继续跑完。
     * 真机上留下过一个 99% CPU 的孤儿，而那次请求早已回报超时。
     *
     * 包一层是为了拿到那条命令的 pid：命令放进子 shell 后台跑，pid 落到本次命令专属的
     * 文件里，超时后由 [terminateRunningCommand] 据它终止。
     *
     * `< /dev/null` 不是可选项：后台任务默认继承常驻 shell 的 stdin，一条读 stdin 的命令
     * 会把**后续命令**从管道里吃掉，之后每条都得等满超时。
     *
     * 退出码与标记由同一次 echo 输出，两者必然同行。`$?` 必须紧贴着它要反映的那条命令
     * （这里中间多了一步 `rm`，所以先存进 `__ntu_rc`），而贴在标记上之后，"读到标记"与
     * "拿到退出码"就是同一件事了——分两行写过一版，真机上被 `head -c` 打穿过：那条命令
     * 的输出没有结尾换行，标记粘在内容后面，退出码那一行跟着错位，成败就读不出来了。
     */
    internal fun wrap(command: String, marker: String, pidFile: String): String =
        "( $command ) < /dev/null & __ntu=\$!; echo \$__ntu > $pidFile; " +
            "wait \$__ntu; __ntu_rc=\$?; rm -f $pidFile; echo $marker\$__ntu_rc\n"

    /**
     * 终止那次超时的命令。
     *
     * 走 [RootShell.execDetached]：此刻本通道已经不可信，只能用一条不经过它的独立进程。
     *
     * 依据的是**本次命令专属**的 pid 文件，读不到就什么都不做——命令还没走到写 pid 那一步
     * 时，杀掉上一条命令残留的 pid 比不杀更糟。
     */
    private fun terminateRunningCommand(pidFile: String) {
        RootShell.execDetached("kill -9 \$(cat $pidFile 2>/dev/null) 2>/dev/null; rm -f $pidFile")
    }

    /** 销毁本通道的进程。下次 exec 会重新拉起。 */
    @Synchronized
    fun destroy() {
        resetShell()
    }

    private fun resetShell() {
        try {
            process?.destroy()
        } catch (_: Exception) {
            // 进程可能已退出
        }
        process = null
        stdin = null
        stdout = null
    }

    companion object {
        /**
         * 单条常驻命令的默认等待上限；长命令由调用方显式放宽，或改走脱手执行。
         *
         * 必须是 public：默认参数在**调用方**求值，引用私有成员编译不过。
         */
        const val DEFAULT_TIMEOUT_MS = 20_000L

        /**
         * 拉起常驻进程的上限。
         *
         * `su` 正常启动不到一秒，给到 10 秒是留给设备过载的余量——真机上 loadavg 长期在 7~8
         * 之间（八核），而这个应用的每次界面识别还要再压上几十毫秒。
         */
        private const val SHELL_START_TIMEOUT_MS = 10_000L

        private const val TAG = "ShellChannel"
    }
}
