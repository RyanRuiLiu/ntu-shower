package com.ryanliu.ntushower.core.config

/** 设备环境锚点：目标设备上的固定事实（浴室 App、其数据库位置与本应用工作目录）。 */
object AppConfig {
    const val BATH_APP_PACKAGE = "com.datalook.bath"
    const val BATH_DB_DIR = "/sdcard/datalook/db"
    const val BATH_DB_FILE = "bath.db"
    const val BATH_DB_PATH = "$BATH_DB_DIR/$BATH_DB_FILE"

    /** 本应用在共享存储上的工作目录（OTA 下载、崩溃日志）。 */
    const val WORK_DIR = "/sdcard/ntu-shower"
}
