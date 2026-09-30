package com.ryanliu.ntushower.core.shell

/**
 * 常驻 shell 的输出解析（纯逻辑，可单测）。
 *
 * 命令输出与后续命令共用同一条流，靠标记行切分：读到标记即该命令结束。未遇到标记说明命令
 * 仍在执行（超时由调用方负责），返回 null 而不是部分结果——部分结果会让调用方误以为命令
 * 已完成。
 *
 * 超限时**继续读到标记行**、只是不再累积：这条流归后续命令共用，提前退出会把残余输出留给
 * 下一条命令读。真机上出过事——一条 `dumpsys` 的输出被整块读进内存，agent 进程当场被系统
 * 杀掉、设备从此失联。
 *
 * ## 退出码为什么贴在标记后面
 *
 * 标记与退出码由**同一次 `echo`** 输出（`echo <标记>$?`），两者必然同行。早先分两行写，
 * 真机上立刻出了事：`head -c` 的输出不以换行结尾，标记就和内容粘成一行，退出码那一行跟着
 * 错位——摘不到退出码，而调用方只能猜命令成没成。合并成一行之后，"读到标记"与"拿到退出码"
 * 是同一件事，不可能只发生一半。
 *
 * 同一处也解释了标记为什么**按子串定位**而不是按整行匹配：内容没有结尾换行时，标记就贴在
 * 内容后面。按整行匹配的实现会把那条命令的输出和退出码一起吞掉。
 */
object ShellOutput {

    /** 达到上限时追加的标注，让调用方分得清"输出就这么多"与"被截断了"。 */
    const val TRUNCATION_NOTE = "\n…（输出达到上限，已截断）"

    /**
     * 退出码读不出来时的取值。
     *
     * 不是 0——0 的含义是"命令自己报告成功"。shell 的退出码落在 0..255，取一个负数就与真实
     * 取值不相交。这条路径事实上到不了：标记行由我们对端注入，格式由我们控制。
     */
    const val UNKNOWN_EXIT_CODE = -1

    /** 一次成功收割的结果：标记之前的内容，以及标记行尾带出的退出码。 */
    data class Captured(val output: String, val exitCode: Int)

    /**
     * 收割标记之前的全部输出，最多累积 `maxBytes` 字节。
     *
     * @return 标记之前的内容（已 trim，超限时带 [TRUNCATION_NOTE]）与退出码；
     *         流先结束也没遇到标记时返回 null
     */
    fun collect(lines: Sequence<String>, marker: String, maxBytes: Int = Int.MAX_VALUE): Captured? {
        val output = StringBuilder()
        var bytes = 0
        var truncated = false
        for (line in lines) {
            val at = line.indexOf(marker)
            if (at >= 0) {
                // 只取标记之前那一段：输出没有结尾换行时，这一行里既有内容也有标记
                output.append(line, 0, at)
                val text = output.toString().trim()
                val exitCode = line.substring(at + marker.length).trim().toIntOrNull() ?: UNKNOWN_EXIT_CODE
                return Captured(if (truncated) text + TRUNCATION_NOTE else text, exitCode)
            }
            // 超限后不再累积、也不再计算长度：这条流可能还有几 MB 要读干，
            // 每行都做一次 UTF-8 编码是白烧 CPU
            if (!truncated) {
                val lineBytes = line.toByteArray(Charsets.UTF_8).size + 1
                if (bytes + lineBytes <= maxBytes) {
                    output.append(line).append('\n')
                    bytes += lineBytes
                } else {
                    truncated = true
                }
            }
        }
        return null
    }
}
