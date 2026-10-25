package com.ryanliu.ntushower.core.log

import java.io.File

/** 一次日志尾部读取的结果；`present=false` 表示设备上还没有这个文件（例如从未崩溃）。 */
data class LogTailResult(
    val content: String,
    val lines: Int,
    val truncated: Boolean,
    val present: Boolean,
    val sizeBytes: Long,
)

/**
 * 读日志文件尾部：按行取最近 [maxLines] 行，最多回传 [MAX_BYTES] 字节。
 *
 * 不调用系统 `tail`——本 ROM 的 toolbox 没有它；文件本身有 256KB 上限，整份读进内存
 * 再按行截取足够。字节切点向前找换行，避免把一个汉字或一条 JSON 从中间劈开。
 */
object LogTail {
    const val MAX_BYTES = 32 * 1024

    fun read(file: File, maxLines: Int, maxBytes: Int = MAX_BYTES): LogTailResult {
        if (!file.isFile) return LogTailResult("", 0, truncated = false, present = false, sizeBytes = 0)
        val size = file.length()
        val bytes = file.readBytes()
        var start = (bytes.size - maxBytes).coerceAtLeast(0)
        if (start > 0) {
            var newline = start
            while (newline < bytes.size && bytes[newline] != '\n'.code.toByte()) newline++
            if (newline < bytes.size) start = newline + 1
        }
        val all = String(bytes, start, bytes.size - start, Charsets.UTF_8)
            .lineSequence()
            .filter { it.isNotBlank() }
            .toList()
        val kept = all.takeLast(maxLines.coerceAtLeast(1))
        return LogTailResult(
            content = kept.joinToString("\n"),
            lines = kept.size,
            truncated = start > 0 || kept.size < all.size,
            present = true,
            sizeBytes = size,
        )
    }
}
