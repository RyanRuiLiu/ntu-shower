package com.ryanliu.ntushower.features.selfmanage

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 数据库归档的纯判定：要不要归档、归档叫什么。
 *
 * 全部是无 IO 的函数——执行（mv 经 root 通道）在 [BathDbArchiver]，这里只回答
 * "做什么"。抽出来是为了让判定逻辑进覆盖率门禁：门禁的赦免清单只该装真正离开
 * JVM 就不存在的代码。
 *
 * 归档文件**永久保留**：那是浴室 App 的完整运营历史，删了回不来（与 AGENTS.md
 * 「历史数据删了就回不来」同一取向），不设任何自动清理。
 */
object BathDbArchivePolicy {

    /** 归档名后缀前缀：`bath.db.arc-20261007-0037`。 */
    const val ARCHIVE_SUFFIX = ".arc-"

    private const val ARCHIVE_SUFFIX_FORMAT = "yyyyMMdd-HHmm"

    /** 阈值判定：大小到线即归档（恰好到线算数）。 */
    fun shouldArchive(sizeBytes: Long, thresholdMb: Long): Boolean = sizeBytes >= thresholdMb * 1024 * 1024

    /** 归档名的时间戳（设备本地时区）：分钟粒度——归档最勤也是一天一次。 */
    fun archiveStamp(now: Date = Date()): String = SimpleDateFormat(ARCHIVE_SUFFIX_FORMAT, Locale.US).format(now)
}
