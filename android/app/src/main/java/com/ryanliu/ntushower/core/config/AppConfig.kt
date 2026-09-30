package com.ryanliu.ntushower.core.config

/** 设备环境锚点：目标设备上的固定事实（浴室 App、其数据库位置与本应用工作目录）。 */
object AppConfig {
    const val BATH_APP_PACKAGE = "com.datalook.bath"
    const val BATH_DB_DIR = "/sdcard/datalook/db"
    const val BATH_DB_FILE = "bath.db"
    const val BATH_DB_PATH = "$BATH_DB_DIR/$BATH_DB_FILE"

    /** 本应用在共享存储上的工作目录（OTA 下载、崩溃日志）。 */
    const val WORK_DIR = "/sdcard/ntu-shower"

    /**
     * 本应用自己的数据库：待确认快照的发件箱。
     *
     * **设备上唯一属于我们的数据**。另外两个库都是别人的：`bath.db` 是浴室 App 的
     * （只读），`settings.db` 是 Android 系统的（只在无障碍开关被拒绝写回时动它）。
     *
     * 落在**应用私有目录**（`openOrCreateDatabase` 的默认位置
     * `/data/data/<包名>/databases/`）而不是共享存储——发件箱里装着待上报的喷头快照，
     * 含占用者学号，放 `/sdcard` 上任何应用都读得到。
     */
    const val OUTBOX_DB_FILE = "door_outbox.db"

    /** 发件箱表名：与库同名，库里只有这一张表。 */
    const val OUTBOX_TABLE = "door_outbox"
}
