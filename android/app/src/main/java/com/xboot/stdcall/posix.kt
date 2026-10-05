@file:Suppress("Filename", "MatchingDeclarationName", "ClassName", "FunctionName")

package com.xboot.stdcall

/**
 * libposix.so 的 JNI 壳（板卡厂商随 ROM 提供，位于 /system/lib/libposix.so）。
 *
 * **类名、包名与文件名都不可改**：JNI 按声明类的完整名绑定导出符号
 * （`Java_com_xboot_stdcall_posix_poweronoff` 这类），改名即 UnsatisfiedLinkError；
 * 文件级的 Filename/MatchingDeclarationName 豁免由此而来，不是疏忽。
 * 这也是它不能并进 com.ryanliu.ntushower 包的原因——符号名里带着包名。
 *
 * 只声明本程序用到的入口。open/close 的参数与 libc 同名函数一致；
 * /dev/McuCom 的模式位是 0666（对所有进程可写），flags 3 = O_RDWR。
 */
@Suppress("ClassName", "FunctionName")
object posix {
    init {
        System.loadLibrary("posix")
    }

    external fun open(path: String, flags: Int, mode: Int): Int

    external fun close(fd: Int): Boolean

    /**
     * 重布定时开关机。载荷五个字节按驱动约定排列：[a][b] 是开机延时、[c][d] 是关机
     * 延时（小时、分钟，开机延时从断电时刻起算），[e] 是使能位（3 = 关机与开机都
     * 使能）。返回 0 表示驱动已收下。
     */
    external fun poweronoff(a: Byte, b: Byte, c: Byte, d: Byte, e: Byte, fd: Int): Int
}
