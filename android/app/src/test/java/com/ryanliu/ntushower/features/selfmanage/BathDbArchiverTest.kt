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
        assertTrue(BathDbArchiver.shouldArchive(20 * mb, thresholdMb = 20))
        assertFalse(BathDbArchiver.shouldArchive(20 * mb - 1, thresholdMb = 20))
        // 空文件/文件不存在（size 0）永不触发
        assertFalse(BathDbArchiver.shouldArchive(0, thresholdMb = 20))
    }

    @Test
    fun `threshold of one megabyte archives anything at or above one mb`() {
        assertTrue(BathDbArchiver.shouldArchive(mb, thresholdMb = 1))
        assertFalse(BathDbArchiver.shouldArchive(mb - 1, thresholdMb = 1))
    }

    @Test
    fun `archive stamp is minute grained and sortable`() {
        // 设备本地时区（操作员对照的是设备墙钟）；分钟粒度——归档最勤也是一天一次
        val calendar = Calendar.getInstance()
        calendar.clear()
        calendar.set(2026, Calendar.OCTOBER, 7, 0, 37, 0)

        val stamp = BathDbArchiver.archiveStamp(Date(calendar.timeInMillis))

        assertEquals("20261007-0037", stamp)
    }
}
