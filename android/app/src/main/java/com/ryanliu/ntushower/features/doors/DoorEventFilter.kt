package com.ryanliu.ntushower.features.doors

/**
 * 数据库目录事件过滤（纯逻辑，可单测）。
 *
 * 监听目录里还有其他写入（其他文件、临时文件），只有目标数据库本体与其
 * **回滚日志**才代表一次真实写入。真机核实（2026-09-30）：浴室 App 用
 * rollback journal 模式，目录内只有 `bath.db` 与 `bath.db-journal`，无 `-wal`。
 */
object DoorEventFilter {

    /** 该路径是否值得触发一次采集。 */
    fun isRelevant(path: String?, dbFile: String): Boolean {
        if (path == null) return false
        return path == dbFile || path == "$dbFile-journal"
    }
}
