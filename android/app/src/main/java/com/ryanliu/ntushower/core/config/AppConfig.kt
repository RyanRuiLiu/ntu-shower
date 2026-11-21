package com.ryanliu.ntushower.core.config

/** 设备环境锚点：目标设备上的固定事实（浴室 App、它的数据库与名单文件、本应用工作目录）。 */
object AppConfig {
    const val BATH_APP_PACKAGE = "com.datalook.bath"

    /**
     * 浴室 App 的主界面（`BathMainActivity`）。
     *
     * 与 [BATH_APP_PACKAGE] 同样是对端 manifest 定下的事实，不由我们决定。
     *
     * **它没有 intent-filter，所以 `exported=false`——普通应用起不来它。** 真机日志是
     * `Permission Denial: ... cmp=com.datalook.bath/.BathMainActivity ... not exported from
     * uid 10053`。拉起它只能经 root shell（shell 持有 `START_ANY_ACTIVITY`），见
     * `A11yService.launchBathMainActivity`。LAUNCHER 入口 `WelcomeActivity` 带 intent-filter、
     * 隐式导出，普通应用起得来，但它要联网下载参数，10 秒以上才进得去浴室页。
     */
    const val BATH_MAIN_ACTIVITY = "com.datalook.bath.BathMainActivity"

    /**
     * 设备上算「桌面」的包：浴室 App 退出后落到它们之一，才算真的有人离开了浴室界面。
     *
     * 这台设备的桌面是**新开普的集中控制端**（它注册了 `CATEGORY_HOME`，真机实测按 HOME
     * 落到它），系统自带的 `com.android.launcher` 一并列出。
     *
     * **为什么需要这个判据**：「退到桌面」与「退到别的包」在退出事件上同形，只能由去向区分，
     * 而两者该有相反的处理——退到桌面是有人想离开，要立刻拉回来；退到输入法设置之类的包是
     * 现场人员走调试通道（设备断网时远程失效、又插不了线，这条通道是刻意留的），拉回来
     * 等于把它废掉。
     */
    val BATH_DESKTOP_PACKAGES = setOf(
        "com.newcapec.control.update",
        "com.android.launcher",
    )

    const val BATH_DB_DIR = "/sdcard/datalook/db"
    const val BATH_DB_FILE = "bath.db"
    const val BATH_DB_PATH = "$BATH_DB_DIR/$BATH_DB_FILE"

    /**
     * 浴室 App 的 SharedPreferences（`MODE_PRIVATE`，需 root 读）。
     *
     * 水控板直控只用其中的 `waterList`（板 IP/端口/起始门号/门数/编码/名称）与
     * `BathControlPosno`（协议 POS）。**不要读 `usercode`/`seedkey`**——那是制卡密钥，
     * 与授权出水无关；整份 XML 也不进日志。
     *
     * 同一文件里还有旧默认值 `BathControlIP=192.168.16.222`、`DoorCount=48`，那些
     * 不是服务器下发的 waterList，水控板直控明确不读它们。
     */
    const val BATH_PREFS_PATH = "/data/data/$BATH_APP_PACKAGE/shared_prefs/data.xml"

    /**
     * 浴室 App 读的免费名单：一行一个学号，名单里的人不收费。
     *
     * 路径写在这里而不由服务端下发——服务端只给内容。**给得了路径就等于把"写哪个
     * 文件"交给对端**，而这份名单的全部安全性正来自"只有这一个文件可写"。
     */
    const val BATH_FREELIST_PATH = "/sdcard/datalook/readfile/free.txt"

    /** 本应用在共享存储上的工作目录（OTA 下载、崩溃日志）。 */
    const val WORK_DIR = "/sdcard/ntu-shower"

    /** 未捕获异常堆栈：崩溃处理器写入，重连后随 device/crash 一起上送。 */
    const val CRASH_LOG_NAME = "crash.log"

    /** OTA 脱手安装脚本的原始输出（`pm install` 的结论只在这里）。 */
    const val OTA_INSTALL_LOG_NAME = "ota-install.log"

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
