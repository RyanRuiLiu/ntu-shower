package com.ryanliu.ntushower.features.selfmanage

import android.util.Log
import com.ryanliu.ntushower.core.config.AppConfig
import com.ryanliu.ntushower.core.config.RuntimeConfig
import com.ryanliu.ntushower.core.log.DeviceLog
import com.ryanliu.ntushower.core.shell.RootShell
import java.io.File

/**
 * 浴室 App 数据库归档的执行者：bath.db 随会话只增不减（7 个常驻 fd 的 SQLite 长连接
 * + 回滚日志），涨到几十 MB 后 App 明显卡顿。归档即把它移开——浴室 App 下次打开
 * 原路径时自动建一个全新的空库，节奏回到零。
 *
 * ## 时机：本进程每次启动时评估一次
 *
 * 每晚 23:58 断电、次日 10:55 上电，App 与本程序都随之重启——开机是唯一确定
 * "没有会话在进行"的窗口（刚上电，学生还没来得及分配）。阈值判断按当前库大小，
 * 攒够了才动手，平时一次 `stat` 的开销。
 *
 * ## 改名对运行中的 App 无害（2026-10-07 4F 实测）
 *
 * App 对 bath.db 持有 7 个常驻 fd：改名后 fd 跟随 inode，App 照常写，它自己毫无
 * 感知；新库要等 App 下次打开原路径才出现（夜里断电重启必然发生）。journal 没有
 * 常驻 fd，先移 journal 再移 db——任何中间态都不会出现"旧日志配新库"的损坏。
 *
 * ## 归档永久保留
 *
 * 归档文件是浴室 App 的完整运营历史，删了回不来——**不设任何自动清理**。判定逻辑
 * （阈值/命名）在 [BathDbArchivePolicy] 并进覆盖率；本类只做 shell 与文件系统编排，
 * 与 RootShell 同例不进赦免清单。
 */
object BathDbArchiver {

    private const val ARCHIVE_THRESHOLD_MB = 10L
    private const val TAG = "BathDbArchiver"

    private val deviceLog = DeviceLog(File(AppConfig.WORK_DIR))

    /** 本进程启动时调一次：攒够就归档，没攒够只是一次文件大小查询。 */
    fun runIfNeeded() {
        if (!RuntimeConfig.bathDbArchiveEnabled) return
        val db = File(AppConfig.BATH_DB_PATH)
        val size = if (db.exists()) db.length() else 0L
        if (!BathDbArchivePolicy.shouldArchive(size, ARCHIVE_THRESHOLD_MB)) return
        archive(size)
    }

    /** 执行归档：journal 先移、db 后移（中间态安全，见类注释）。归档永久保留。 */
    private fun archive(sizeBytes: Long) {
        val suffix = BathDbArchivePolicy.ARCHIVE_SUFFIX + BathDbArchivePolicy.archiveStamp()
        val script = buildString {
            append("cd ").append(AppConfig.BATH_DB_DIR)
            // journal 先移：journal 在而 db 不在的中间态不存在；db 在而 journal 不在
            // 只会丢回滚能力（归档库可接受），绝不会让旧日志配新库
            for (name in listOf(AppConfig.BATH_DB_FILE + "-journal", AppConfig.BATH_DB_FILE)) {
                append(" && [ -f ").append(name).append(" ] && mv ")
                    .append(name).append(' ').append(name).append(suffix)
            }
            append("; ls ")
            append(AppConfig.BATH_DB_FILE).append(BathDbArchivePolicy.ARCHIVE_SUFFIX).append('*')
            append(" 2>/dev/null")
        }
        val result = RootShell.control.exec(script)
        if (result.exitCode != 0 || !result.output.contains(AppConfig.BATH_DB_FILE)) {
            deviceLog.error("bath-db.archive-failed", "exit=${result.exitCode}")
            Log.e(TAG, "archive failed: exit=${result.exitCode}")
            return
        }
        deviceLog.lifecycle("bath-db.archived", "size=$sizeBytes suffix=$suffix")
        Log.i(TAG, "archived ${sizeBytes / 1024}KB as *$suffix")
    }
}
