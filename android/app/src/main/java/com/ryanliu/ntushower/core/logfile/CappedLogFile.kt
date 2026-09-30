package com.ryanliu.ntushower.core.logfile

import java.io.File

/**
 * 有上限的追加式日志文件。
 *
 * 超过 `maxBytes` 时**丢掉最旧的一半**，而不是清空整个文件。清空会把崩溃循环开始时的
 * 那几条一起抹掉，而恰恰是那几条说明了「这台机器当时在做什么」——留下的后半段反而更接近
 * 症状本身，排查的人却看不到是什么把它引到那一步的。
 *
 * 写失败一律吞掉：调用它的两处（崩溃处理器、自动化关键路径）都比日志本身重要，日志写不
 * 进去不该把那里拖垮。
 */
object CappedLogFile {

    /** 追加一段文本；超限时先裁掉最旧的一半。 */
    fun append(file: File, text: String, maxBytes: Long) {
        try {
            if (file.length() > maxBytes) trim(file, maxBytes)
            file.appendText(text)
        } catch (_: Exception) {
            // 见类注释：日志写入失败不该影响调用方
        }
    }

    /** 只留最近的一半。 */
    private fun trim(file: File, maxBytes: Long) {
        val keepBytes = (maxBytes / 2).toInt()
        val all = file.readBytes()
        if (all.size <= keepBytes) return
        var start = all.size - keepBytes
        // 从切点往后找换行：按字节硬切会把一个汉字劈成两半，在文件开头留一个坏字符
        while (start < all.size && all[start] != '\n'.code.toByte()) start++
        if (start < all.size) start++
        file.writeBytes(all.copyOfRange(start, all.size))
    }
}
