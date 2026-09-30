package com.ryanliu.ntushower.features.doors

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DoorEventFilterTest {

    private val dbFile = "bath.db"

    @Test
    fun `accepts the database file itself`() {
        assertTrue(DoorEventFilter.isRelevant("bath.db", dbFile))
    }

    @Test
    fun `accepts the rollback journal`() {
        // 事务写入主要体现为 journal 变化，漏掉它等于采集失灵（真机实测教训）
        assertTrue(DoorEventFilter.isRelevant("bath.db-journal", dbFile))
    }

    @Test
    fun `rejects null path`() {
        // FileObserver 对目录级事件可能给 null path
        assertFalse(DoorEventFilter.isRelevant(null, dbFile))
    }

    @Test
    fun `rejects unrelated files in the directory`() {
        assertFalse(DoorEventFilter.isRelevant("other.db", dbFile))
        assertFalse(DoorEventFilter.isRelevant("bath.db-wal", dbFile))
        assertFalse(DoorEventFilter.isRelevant("bath.db.bak", dbFile))
    }

    @Test
    fun `does not match by prefix`() {
        // 前缀匹配会让 bath.db2 这类文件误触发采集
        assertFalse(DoorEventFilter.isRelevant("bath.db2", dbFile))
        assertFalse(DoorEventFilter.isRelevant("xbath.db", dbFile))
    }
}
