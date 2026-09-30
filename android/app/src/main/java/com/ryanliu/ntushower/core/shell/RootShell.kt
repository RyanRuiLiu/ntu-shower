package com.ryanliu.ntushower.core.shell

import android.util.Log
import java.io.BufferedReader
import java.io.DataOutputStream
import java.io.InputStreamReader
import java.util.UUID

/**
 * 常驻 root shell：进程只 spawn 一次，命令经 stdin 持续下发，
 * 以标记行分隔输出（旧系统真机验证过的模式）。
 *
 * 为什么不用"每次命令独立 spawn"：测量显示 spawn 成本占主导（~100ms/次）；
 * 常驻进程后单命令降为毫秒级。线程安全由 @Synchronized 串行化保证。
 */
object RootShell {

    data class Result(val success: Boolean, val output: String)

    private const val TAG = "RootShell"

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
                    process!!.errorStream.readBytes() // 丢弃 stderr，防止缓冲区塞满
                } catch (_: Exception) {}
            }.start()
            true
        } catch (e: Exception) {
            Log.e(TAG, "failed to start su: ${e.message}")
            false
        }
    }

    @Synchronized
    fun exec(command: String): Result {
        if (!ensureShell()) {
            return Result(false, "root shell unavailable")
        }
        return try {
            val marker = "__DONE_${System.nanoTime()}_${UUID.randomUUID().toString().take(8)}__"
            stdin!!.writeBytes("$command\n")
            stdin!!.writeBytes("echo $marker\n")
            stdin!!.flush()

            val output = StringBuilder()
            generateSequence { stdout!!.readLine() }
                .takeWhile { it != marker }
                .forEach { output.append(it).append('\n') }
            Result(true, output.toString().trim())
        } catch (e: Exception) {
            // 管道断裂：销毁进程，下次调用重新拉起
            try {
                process?.destroy()
            } catch (_: Exception) {}
            process = null
            stdin = null
            stdout = null
            Result(false, e.message ?: "shell broken")
        }
    }

    @Synchronized
    fun destroy() {
        try {
            process?.destroy()
        } catch (_: Exception) {}
        process = null
        stdin = null
        stdout = null
    }
}
