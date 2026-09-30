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
 * 让门状态晚上报同样长的时间。该分几条、按什么分，见 [RootShell] 的类注释。
 *
 * 超时是必须的：`readLine()` 在命令不返回时会永久阻塞，而这里是串行的——一条
 * 挂住的命令（等输入、卡在内核）会冻结本通道的全部调用，进而拖死它的使用者。
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

    data class Result(val success: Boolean, val output: String)

    private var process: Process? = null
    private var stdin: DataOutputStream? = null
    private var stdout: BufferedReader? = null

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
        return try {
            process = Runtime.getRuntime().exec("su")
            stdin = DataOutputStream(process!!.outputStream)
            stdout = BufferedReader(InputStreamReader(process!!.inputStream))
            Thread {
                try {
                    process?.errorStream?.readBytes() // 丢弃 stderr，防止缓冲区塞满
                } catch (_: Exception) {
                    // stderr 读取失败不影响命令执行
                }
            }.start()
            true
        } catch (e: Exception) {
            Log.e(TAG, "[$name] failed to start su: ${e.message}")
            false
        }
    }

    @Synchronized
    fun exec(command: String, timeoutMs: Long = DEFAULT_TIMEOUT_MS): Result {
        if (!ensureShell()) {
            return Result(false, "root shell unavailable")
        }
        val marker = "__DONE_${System.nanoTime()}_${UUID.randomUUID().toString().take(8)}__"
        return try {
            stdin!!.writeBytes("$command\n")
            stdin!!.writeBytes("echo $marker\n")
            stdin!!.flush()
            when (val output = readUntil(marker, timeoutMs)) {
                null -> Result(false, "command timed out after ${timeoutMs}ms")
                else -> Result(true, output)
            }
        } catch (e: Exception) {
            // 管道断裂：销毁进程，下次调用重新拉起
            resetShell()
            Result(false, e.message ?: "shell broken")
        }
    }

    /**
     * 读到标记行为止；超时或读取异常返回 null。
     *
     * 超时路径必须销毁进程：读线程正阻塞在 readLine 上，只有关闭管道才能让它退出
     * （否则每次超时都泄漏一个阻塞线程）。
     */
    private fun readUntil(marker: String, timeoutMs: Long): String? {
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
            Log.w(TAG, "[$name] command timed out after ${timeoutMs}ms, recycling shell")
            resetShell()
            null
        } catch (e: Exception) {
            Log.w(TAG, "[$name] read failed: ${e.message}, recycling shell")
            resetShell()
            null
        }
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

        private const val TAG = "ShellChannel"
    }
}
