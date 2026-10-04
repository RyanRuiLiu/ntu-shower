package com.ryanliu.ntushower.core.config

/** 设备环境锚点：目标设备上的固定事实（浴室 App、它的数据库与名单文件、本应用工作目录）。 */
object AppConfig {
    const val BATH_APP_PACKAGE = "com.datalook.bath"
    const val BATH_DB_DIR = "/sdcard/datalook/db"
    const val BATH_DB_FILE = "bath.db"
    const val BATH_DB_PATH = "$BATH_DB_DIR/$BATH_DB_FILE"

    /**
     * 浴室 App 读的免费名单：一行一个学号，名单里的人不收费。
     *
     * 路径写在这里而不由服务端下发——服务端只给内容。**给得了路径就等于把"写哪个
     * 文件"交给对端**，而这份名单的全部安全性正来自"只有这一个文件可写"。
     */
    const val BATH_FREELIST_PATH = "/sdcard/datalook/readfile/free.txt"

    /** 本应用在共享存储上的工作目录（OTA 下载、崩溃日志）。 */
    const val WORK_DIR = "/sdcard/ntu-shower"

    /**
     * 本应用自己的数据库。
     *
     * **设备上唯一属于我们的数据**。另外两个库都是别人的：`bath.db` 是浴室 App 的
     * （只读），`settings.db` 是 Android 系统的（只在无障碍开关被拒绝写回时动它）。
     *
     * 落在**应用私有目录**（`openOrCreateDatabase` 的默认位置
     * `/data/data/<包名>/databases/`）而不是共享存储——发件箱里装着待上报的喷头帧，
     * 含占用者学号，放 `/sdcard` 上任何应用都读得到。
     *
     * 名字取 `agent` 而不是某张表的名字：库里装着两张不相干的表（见下），拿其中一张命名
     * 会让另一张看起来像走错了地方。
     */
    const val AGENT_DB_FILE = "agent.db"

    /** 待确认的喷头帧：逐条发送，收到 ack 才删。 */
    const val OUTBOX_TABLE = "door_outbox"

    /**
     * 服务端最后下发的配置，只存一行。
     *
     * 供下次启动直接使用：设备重启后要过 DNS、TLS 与首帧往返才拿得到新配置，中间那几秒
     * 该用上次的值，而不是回落到编译进 APK 的默认值。
     */
    const val CONFIG_TABLE = "config_effective"
}
