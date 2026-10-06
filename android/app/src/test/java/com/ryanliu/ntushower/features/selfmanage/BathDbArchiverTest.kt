package com.ryanliu.ntushower.features.selfmanage

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar
import java.util.Date

class BathDbArchiverTest {

    private val mb = 1024L * 1024L

    @Test
    fun `archives only when the size reaches the threshold`() {
        // 恰好到线即归档（>=）；差一点不归档
        assertTrue(BathDbArchivePolicy.shouldArchive(20 * mb, thresholdMb = 20))
        assertFalse(BathDbArchivePolicy.shouldArchive(20 * mb - 1, thresholdMb = 20))
        // 空文件/文件不存在（size 0）永不触发
        assertFalse(BathDbArchivePolicy.shouldArchive(0, thresholdMb = 20))
    }

    @Test
    fun `threshold of one megabyte archives anything at or above one mb`() {
        assertTrue(BathDbArchivePolicy.shouldArchive(mb, thresholdMb = 1))
        assertFalse(BathDbArchivePolicy.shouldArchive(mb - 1, thresholdMb = 1))
    }

    @Test
    fun `prune keeps the newest keep entries and never the freshly created one`() {
        val names = listOf(
            "bath.db.arc-20261005-1055",
            "bath.db.arc-20261006-1055",
            "bath.db.arc-20261007-0037",
            "bath.db.arc-20261007-1055",
        )

        val toPrune = BathDbArchivePolicy.archivesToPrune(
            names,
            keep = BathDbArchivePolicy.ARCHIVE_KEEP,
            newestSuffix = "20261007-1055",
        )

        // 只删最旧的一份；刚生成的（keep 窗口内）永不在列
        assert(toPrune == listOf("bath.db.arc-20261005-1055"))
    }

    @Test
    fun `archive stamp is minute grained and sortable`() {
        // 设备本地时区（操作员对照的是设备墙钟）；分钟粒度——归档最勤也是一天一次
        val calendar = Calendar.getInstance()
        calendar.clear()
        calendar.set(2026, Calendar.OCTOBER, 7, 0, 37, 0)

        val stamp = BathDbArchivePolicy.archiveStamp(Date(calendar.timeInMillis))

        assertEquals("20261007-0037", stamp)

        // 无参重载（默认取当前时刻）也要能跑：归档执行者走的就是它
        assertEquals(13, BathDbArchivePolicy.archiveStamp().length)
    }
}
