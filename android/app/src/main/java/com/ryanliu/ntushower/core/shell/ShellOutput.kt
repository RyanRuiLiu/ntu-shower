package com.ryanliu.ntushower.core.shell

/**
 * 常驻 shell 的输出解析（纯逻辑，可单测）。
 *
 * 命令输出与后续命令共用同一条流，靠标记行切分：读到标记即该命令结束。
 * 未遇到标记说明命令仍在执行（超时由调用方负责），返回 null 而不是部分结果——
 * 部分结果会让调用方误以为命令已完成。
 *
 * 超限时**继续读到标记行**、只是不再累积：这条流归后续命令共用，提前退出会把残余
 * 输出留给下一条命令读。真机上出过事——一条 `dumpsys` 的输出被整块读进内存，
 * agent 进程当场被系统杀掉、设备从此失联。
 */
object ShellOutput {

    /** 达到上限时追加的标注，让调用方分得清"输出就这么多"与"被截断了"。 */
    const val TRUNCATION_NOTE = "\n…（输出达到上限，已截断）"

    /**
     * 收集标记行之前的全部输出，最多累积 `maxBytes` 字节。
     *
     * @return 标记之前的内容（已 trim，超限时带 [TRUNCATION_NOTE]）；
     *         流先结束也没遇到标记时返回 null
     */
    fun collect(lines: Sequence<String>, marker: String, maxBytes: Int = Int.MAX_VALUE): String? {
        val output = StringBuilder()
        var bytes = 0
        var truncated = false
        for (line in lines) {
            if (line == marker) {
                val text = output.toString().trim()
                return if (truncated) text + TRUNCATION_NOTE else text
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
