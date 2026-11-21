package com.ryanliu.ntushower.core.shell

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ShellChannelTest {

    private val marker = "__DONE_123__"
    private val pidFile = "/data/local/tmp/ntushower-command-123.pid"

    @Test
    fun `wrapped command survives utf8 round trip`() {
        // 这条锁的是一个真实故障：下发用的是 DataOutputStream.writeBytes(String)，而它的
        // 语义是逐字符写出并**丢弃高 8 位**。中文命令因此变成 ASCII 噪声——真机上
        // `echo 测试` 实际执行的是 `echo K-`。
        //
        // wrap 本身是纯函数，问题出在把它写进管道的那一步；这里验证"我们送进去的字节"
        // 在 UTF-8 下能还原出原命令，而 writeBytes 那种做法必然还原不出。
        val command = "echo 测试"
        val bytes = ShellChannel("test").wrap(command, marker, pidFile).toByteArray(Charsets.UTF_8)

        val utf8 = "测试".toByteArray(Charsets.UTF_8)
        assertArrayEquals(
            "命令里的中文必须以 UTF-8 字节原样送达；丢了高 8 位就是 writeBytes 那种写法",
            utf8,
            bytes.copyOfRange(
                indexOf(bytes, utf8),
                indexOf(bytes, utf8) + utf8.size,
            ),
        )
    }

    @Test
    fun `wrapped command keeps the terminable shape`() {
        // 命令被包进子 shell 后台跑，是为了拿到 pid 以便超时后按 pid 杀掉它——设备上的
        // 真实进程链是 su → su → su → sh，只 destroy 最上面那层杀不掉真正在跑的那个。
        // `< /dev/null` 也不是可选项：后台任务继承常驻 shell 的 stdin 时，一条读 stdin
        // 的命令会把后续命令从管道中读走。
        val wrapped = ShellChannel("test").wrap("sleep 99", marker, pidFile)

        assertTrue("命令该放进子 shell 后台执行，才拿得到 pid", wrapped.startsWith("( sleep 99 ) < /dev/null &"))
        assertTrue("pid 该落到本次命令专属的文件", wrapped.contains("echo \$__ntu > $pidFile"))
        assertTrue("退出码该与标记同行输出，否则成败读不出来", wrapped.contains("echo $marker\$__ntu_rc"))
        assertTrue("该以换行收尾，否则常驻 shell 不会执行它", wrapped.endsWith("\n"))
    }

    @Test
    fun `wrapped command passes shell metacharacters through untouched`() {
        // 命令本身含引号、重定向、管道、$ 都是常见的（远程命令是任意内容）。
        // wrap 只做前后包裹，不得改写命令体。
        val command = "cat /sdcard/a.txt | grep 'x y' > /sdcard/b.txt && echo \$HOME"
        val wrapped = ShellChannel("test").wrap(command, marker, pidFile)

        assertTrue("命令体必须原样出现在包裹里", wrapped.contains(command))
        assertEquals("除了前后包裹，不该多出别的 $ 展开", 1, Regex(Regex.escape("( $command )")).findAll(wrapped).count())
    }

    private fun indexOf(haystack: ByteArray, needle: ByteArray): Int {
        for (i in 0..haystack.size - needle.size) {
            if (needle.indices.all { haystack[i + it] == needle[it] }) return i
        }
        return -1
    }
}
