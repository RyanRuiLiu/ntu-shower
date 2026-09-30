package com.ryanliu.ntushower.core.shell

/**
 * 常驻 shell 的输出解析（纯逻辑，可单测）。
 *
 * 命令输出与后续命令共用同一条流，靠标记行切分：读到标记即该命令结束。
 * 未遇到标记说明命令仍在执行（超时由调用方负责），返回 null 而不是部分结果——
 * 部分结果会让调用方误以为命令已完成。
 */
object ShellOutput {

    /**
     * 收集标记行之前的全部输出。
     *
     * @return 标记之前的内容（已 trim）；流先结束也没遇到标记时返回 null
     */
    fun collect(lines: Iterator<String>, marker: String): String? {
        val output = StringBuilder()
        while (lines.hasNext()) {
            val line = lines.next()
            if (line == marker) return output.toString().trim()
            output.append(line).append('\n')
        }
        return null
    }
}
