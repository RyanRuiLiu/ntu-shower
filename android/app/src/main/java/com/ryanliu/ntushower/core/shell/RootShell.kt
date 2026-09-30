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
 * 常驻 root shell：进程只 spawn 一次，命令经 stdin 持续下发，
 * 以标记行分隔输出（旧系统真机验证过的模式）。
 *
 * 为什么不用"每次命令独立 spawn"：测量显示 spawn 成本占主导（~100ms/次）；
 * 常驻进程后单命令降为毫秒级。线程安全由 @Synchronized 串行化保证。
 *
 * 超时是必须的：`readLine()` 在命令不返回时会永久阻塞，而 exec 是 @Synchronized——
 * 一条挂住的命令（等输入、卡在内核）会冻结全部 root 调用，进而拖死 AgentService。
 */
object RootShell {

    data class Result(val success: Boolean, val output: String)

    private const val TAG = "RootShell"

    /** 单条命令的默认等待上限；长命令（如 pm install）由调用方显式放宽。 */
    const val DEFAULT_TIMEOUT_MS = 20_000L

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
            Log.e(TAG, "failed to start su: ${e.message}")
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
        val task = FutureTask { ShellOutput.collect(reader.lineSequence().iterator(), marker) }
        Thread(task, "root-shell-read").apply {
            isDaemon = true
            start()
        }
        return try {
            task.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (e: TimeoutException) {
            Log.w(TAG, "command timed out after ${timeoutMs}ms, recycling shell")
            resetShell()
            null
        } catch (e: Exception) {
            Log.w(TAG, "read failed: ${e.message}, recycling shell")
            resetShell()
            null
        }
    }

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
}
