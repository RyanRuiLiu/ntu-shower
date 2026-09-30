package com.ryanliu.ntushower.service

/**
 * `input text` 命令的字符串编码。
 *
 * ## 为什么需要单独的编码器
 *
 * `am`/`input` 的参数先经 shell 词法解析，再由 `input` 自己解析一次。
 * 空格必须写成 `%s`——这是 `input` 的约定，不是 shell 转义；而 shell 元字符
 * 还要再套一层单引号。弄错的表现是"输入了一半就断了"或"命令没反应"，
 * 而远端只看到"失败"，很难反查。
 *
 * ## 适用范围
 *
 * 只处理 ASCII。`input text` 在 Android 4.4 上按字节处理，非 ASCII
 * （中文、emoji）会变成乱码或直接失败——这类内容必须走无障碍通道。
 * 判断交给 [isAsciiSafe]，由调用方据此选路。
 */
object ShellTextCodec {

    /** 需要转义的 shell 元字符。单引号包裹后，只有单引号本身仍特殊。 */
    private const val SINGLE_QUOTE = '\''

    /** 内容是否适合走 shell 通道。非 ASCII 一律不适合。 */
    fun isAsciiSafe(text: String): Boolean = text.all { it.code in 0x20..0x7E }

    /**
     * 编码成可安全放进命令行的 `input text` 参数。
     *
     * 空格转 `%s`（`input` 的约定）；整串用单引号包裹，内部的单引号按
     * `'\''` 闭合重开的方式转义。
     *
     * 空串返回 `''`——`input text` 收到空参数会报错，但那是调用方的参数问题，
     * 不在这里伪装成有内容。
     */
    fun encode(text: String): String {
        val withSpaces = text.replace(" ", "%s")
        return "'" + withSpaces.replace(SINGLE_QUOTE.toString(), "'\\''") + "'"
    }
}
