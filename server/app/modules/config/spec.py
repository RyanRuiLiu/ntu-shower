"""远程配置的规格登记表：每个键的类型、范围、归属与说明。

## 为什么需要它

在此之前，配置只有一个扁平的值字典，带来四个问题：

1. **入参无校验**：`heartbeat_interval_s = 0` 会被原样下发，设备按 0 毫秒重发心跳；
2. **未知键静默丢弃**：提交 `{"foo": 1}` 返回成功却什么也没发生；
3. **界面无从自适应**：调用方只拿到值，不知道哪个是布尔、哪个有上下限；
4. **文档只能手写**：键的说明散在文档里，改代码时不会同步。

这里把"有哪些键、各自什么类型与范围、归谁用"变成**可被程序读取的事实**：
服务端据此校验入参，客户端据此渲染控件，文档据此生成。

## 与设备端的关系

`scope=DEVICE` 的键会下发给设备；设备端 `RuntimeConfig` 是它的消费者。两边
各有一份默认值，`tests/test_config_spec.py` 对账二者的键集合，防止单边漂移。
"""

from dataclasses import dataclass
from enum import Enum


class ConfigKind(str, Enum):
    """取值类型。

    用 `str, Enum` 而非 `StrEnum`：运行时是 Python 3.10，后者 3.11 才有。
    """

    INT = "int"
    BOOL = "bool"
    TEXT = "text"
    TEXT_MAP = "text_map"
    """字符串到字符串的映射，目前只有 auto_settle_students 一例。"""


class ConfigScope(str, Enum):
    """归属：键由哪一端消费。"""

    DEVICE = "device"
    """下发给设备，由设备端 RuntimeConfig 应用。"""

    SERVER = "server"
    """仅服务端自己使用，不下发给设备。"""


@dataclass(frozen=True)
class ConfigSpec:
    """一个配置键的完整规格。

    `minimum` / `maximum` 只对 `ConfigKind.INT` 有意义，其余类型为 None。
    """

    key: str
    kind: ConfigKind
    scope: ConfigScope
    default: int | bool | str | dict[str, str]
    title: str
    description: str = ""
    minimum: int | None = None
    maximum: int | None = None
    unit: str = ""


DEVICE = ConfigScope.DEVICE
SERVER = ConfigScope.SERVER
INT = ConfigKind.INT
BOOL = ConfigKind.BOOL
TEXT = ConfigKind.TEXT
TEXT_MAP = ConfigKind.TEXT_MAP

#: 全部配置键的规格。新增配置键时只改这里——服务端校验、客户端渲染、文档
#: 生成都从它派生。
CONFIG_SPECS: tuple[ConfigSpec, ...] = (
    # ── 通信时序 ──
    ConfigSpec(
        "heartbeat_interval_s",
        INT,
        DEVICE,
        30,
        "心跳间隔",
        "设备向服务端上报存活的时间间隔",
        minimum=5,
        maximum=600,
        unit="秒",
    ),
    ConfigSpec(
        "ping_interval_s",
        INT,
        SERVER,
        60,
        "服务端探测间隔",
        "服务端向设备发起 ping 以测量时钟偏差的间隔",
        minimum=10,
        maximum=1800,
        unit="秒",
    ),
    ConfigSpec(
        "clock_jump_threshold_s",
        INT,
        SERVER,
        300,
        "设备时钟跳变阈值",
        "单次观测到的时钟偏差与当前值相差超过它，就判定设备的钟被改过，"
        "直接用新值取代平滑结果。平滑是为了滤掉单次往返的抖动（几十毫秒级），"
        "而设备重启、手动改时间、NTP 生效这类跳变是几十分钟级的——"
        "继续平滑会让中间那几次校正按错误的偏差算，而新值才是事实",
        minimum=30,
        maximum=86400,
        unit="秒",
    ),
    ConfigSpec(
        "bridge_ssh_target",
        TEXT,
        SERVER,
        "deploy@<deploy-host>",
        "调试通道的 SSH 目标",
        "网页端生成本机连接命令时用的 `用户@主机`。它是**现场那台电脑**要 ssh 过去的地方，"
        "与网页自己的域名不是一回事——网页挂在 ntu-shower 子域上，而 ssh 连的是主域，"
        "所以推不出来，只能配。见 docs/RUNBOOK.md 的连接指引。",
    ),
    ConfigSpec(
        "reconnect_base_ms",
        INT,
        DEVICE,
        5000,
        "重连退避基准",
        "断线后首次重连的等待时长，随后按指数退避",
        minimum=500,
        maximum=60000,
        unit="毫秒",
    ),
    ConfigSpec(
        "reconnect_max_ms",
        INT,
        DEVICE,
        60000,
        "重连退避上限",
        "指数退避的封顶值，避免无限增长",
        minimum=5000,
        maximum=600000,
        unit="毫秒",
    ),
    ConfigSpec(
        "watchdog_interval_s",
        INT,
        DEVICE,
        60,
        "看门狗间隔",
        "定时拉起自身服务的间隔（对已存活的服务是幂等操作）",
        minimum=30,
        maximum=3600,
        unit="秒",
    ),
    # ── 喷头状态采集 ──
    ConfigSpec(
        "door_report_debounce_ms",
        INT,
        DEVICE,
        1000,
        "上报去抖窗口",
        "状态变化后等待多久再上报，用于合并短时间内的多次变化",
        minimum=0,
        maximum=60000,
        unit="毫秒",
    ),
    ConfigSpec(
        "outbox_max_rows",
        INT,
        DEVICE,
        100_000,
        "发件箱行数上限",
        "设备侧喷头状态待确认队列的最大行数，超出即丢弃最旧的一行。**默认值刻意给得远超真实所需**："
        "一行约 1KB，十万行约 100MB，而目标设备可用空间 11.9GB（真机实测）——占比不到百分之一，"
        "于是它实际上永远不会触发（离线一星期最多也就积压一万行上下）。"
        "调小它等于主动放弃那段历史：快照丢了设备不会重发，服务端的时长与状态迁移就永久缺一块。",
        minimum=1000,
        maximum=1_000_000,
        unit="行",
    ),
    # ── 自动化遮盖 ──
    ConfigSpec(
        "overlay_enabled",
        BOOL,
        DEVICE,
        True,
        "显示操作遮罩",
        "自动操作期间是否盖住屏幕，防止现场学生误触",
    ),
    ConfigSpec(
        "overlay_message",
        TEXT,
        DEVICE,
        "网络连接中，请稍后重试...",
        "操作遮罩文字",
        "盖屏期间显示的文字。刻意**不写「正在自动操作」**——现场学生看到网络问题会安静等待，"
        "看到「系统正在远程操作这台机器」则可能去拔电源或找来管理员。这句话沿用旧项目",
    ),
    ConfigSpec(
        "overlay_text_size_sp",
        INT,
        DEVICE,
        80,
        "遮罩文字字号",
        "单位为 sp。默认值来自旧项目——那台设备是 1366x768 的横屏终端，字号小了看不清",
        minimum=10,
        maximum=200,
        unit="sp",
    ),
    ConfigSpec(
        "overlay_text_color",
        TEXT,
        DEVICE,
        "#d94f16",
        "遮罩文字颜色",
        "十六进制颜色，如 #d94f16。默认值来自旧项目，与浴室 App 的提示色一致",
    ),
    ConfigSpec(
        "overlay_margin_left_dp",
        INT,
        DEVICE,
        600,
        "遮罩文字左边距",
        "单位为 dp。旧项目把文字推到这个位置，正好落在提示图的右侧空白处",
        minimum=0,
        maximum=2000,
        unit="dp",
    ),
    ConfigSpec(
        "automation_trace_enabled",
        BOOL,
        DEVICE,
        False,
        "记录自动化耗时",
        "把分配/结算流程的分段耗时写进 /sdcard/ntu-shower/automation.log，"
        "用 device.exec 可以直接 cat 取回。默认关：每次记录都要开一次文件，长期开着是"
        "白花的开销，而它的用途本来就是「要量的时候量一轮」",
    ),
    # ── 命令去重 ──
    ConfigSpec(
        "command_dedup_max_count",
        INT,
        DEVICE,
        1024,
        "命令去重窗口",
        "设备侧记住多少条已执行命令的结果。发送方只发一次，接收方仍可能收到多次（重连重发、"
        "网络抖动），而分配与结算是真的再操作一次浴室 App 与硬件，所以必须能认出重复的那条。"
        "**默认给得比够用大得多**：一条记录约 200 字节，1024 条约 200KB，相对设备可用内存可以"
        "忽略；而窗口越大，就越不会出现「结果已被淘汰、同一条命令于是又执行一次」——那一次是"
        "真的重复扣钱。",
        minimum=16,
        maximum=65536,
        unit="条",
    ),
    ConfigSpec(
        "bath_response_timeout_ms",
        INT,
        DEVICE,
        10000,
        "等待服务端响应",
        "自动化流程中等待浴室服务器回应的最长时间",
        minimum=1000,
        maximum=60000,
        unit="毫秒",
    ),
    ConfigSpec(
        "cert_verify_wait_ms",
        INT,
        DEVICE,
        5000,
        "等待身份校验",
        "浴室 App 校验学号密码的等待时长",
        minimum=500,
        maximum=30000,
        unit="毫秒",
    ),
    ConfigSpec(
        "home_timeout_ms",
        INT,
        DEVICE,
        10000,
        "回首页预算",
        "从任意界面返回首页的时间预算",
        minimum=1000,
        maximum=60000,
        unit="毫秒",
    ),
    ConfigSpec(
        "ui_poll_interval_ms",
        INT,
        DEVICE,
        60,
        "界面轮询间隔",
        "页面等待的节奏上限。等待本身是事件驱动的（界面一变就醒），这个值只在"
        "「变化通知没来」时生效——所以它是**最坏情况的上限**，不是固定开销。"
        "60 毫秒对浴室 App 的刷新频率够用：原来取 200，一次分配/结算里五六处等待"
        "各白等一拍，累计就是将近一秒。",
        minimum=50,
        maximum=5000,
        unit="毫秒",
    ),
    ConfigSpec(
        "ui_click_delay_ms",
        INT,
        DEVICE,
        200,
        "点击后延迟",
        "每次点击后等待界面响应的时间",
        minimum=0,
        maximum=5000,
        unit="毫秒",
    ),
    ConfigSpec(
        "ui_dump_max_nodes",
        INT,
        DEVICE,
        2000,
        "节点导出上限",
        "单次导出界面节点树的最大节点数，超出即截断。上限同时决定回传体积，按实测的协议帧容量调整",
        minimum=100,
        maximum=20000,
        unit="个",
    ),
    ConfigSpec(
        "ui_operation_timeout_s",
        INT,
        SERVER,
        30,
        "界面操作超时",
        "服务端等待一次界面操作（点击 / 按键 / 输入 / 导出节点树）回执的上限；设备端另有更短的命令超时",
        minimum=5,
        maximum=300,
        unit="秒",
    ),
    ConfigSpec(
        "ui_swipe_duration_ms",
        INT,
        DEVICE,
        300,
        "滑动默认时长",
        "滑动手势从按下到抬起的时长；过短会被识别成快速抛掷而非拖动",
        minimum=50,
        maximum=5000,
        unit="毫秒",
    ),
    ConfigSpec(
        "device_exec_max_output_bytes",
        INT,
        DEVICE,
        32768,
        "命令输出上限",
        "device.exec 单次回传的最大字节数，超出即截断",
        minimum=1024,
        maximum=1048576,
        unit="字节",
    ),
    # ── 自更新 ──
    ConfigSpec(
        "ota_check_interval_s",
        INT,
        DEVICE,
        3600,
        "检查更新间隔",
        "设备主动查询新版本的时间间隔",
        minimum=60,
        maximum=86400,
        unit="秒",
    ),
    # ── 浴室 App 崩溃守护 ──
    ConfigSpec(
        "bath_app_guard_enabled",
        BOOL,
        DEVICE,
        True,
        "崩溃后拉起浴室 App",
        "浴室 App 异常退出时是否自动重新拉起",
    ),
    ConfigSpec(
        "bath_app_exit_window_ms",
        INT,
        DEVICE,
        30000,
        "退出计数窗口",
        "统计浴室 App 退出次数的时间窗口",
        minimum=1000,
        maximum=600000,
        unit="毫秒",
    ),
    ConfigSpec(
        "bath_app_exit_max_count",
        INT,
        DEVICE,
        3,
        "窗口内最多退出次数",
        "超过该次数即判定为异常退出，按冷却策略处理",
        minimum=1,
        maximum=100,
        unit="次",
    ),
    ConfigSpec(
        "bath_app_exit_cooldown_ms",
        INT,
        DEVICE,
        600000,
        "异常后的冷却时长",
        "判定为异常退出后暂停拉起的时长，避免反复拉起",
        minimum=1000,
        maximum=3600000,
        unit="毫秒",
    ),
    # ── 调试通道 ──
    ConfigSpec(
        "bridge_max_lifetime_s",
        INT,
        SERVER,
        3600,
        "调试通道最长开启时长",
        "从打开算起的最长存活时长，到点强制关闭。**这是唯一的自动关闭条件**——早先另有一个"
        "300 秒的空闲超时，而它的判据错了：设备侧的隧道连进来之后，在我们这边还没执行"
        "`adb connect` 之前一个字节都不会发，于是开桥五分钟后就自己关了，而人还以为它在。"
        "调试的节奏由人定，中途停下来想事情、去装 adb 都正常，不该被打断",
        minimum=300,
        maximum=86400,
        unit="秒",
    ),
    ConfigSpec(
        "bridge_pair_timeout_s",
        INT,
        SERVER,
        30,
        "调试通道配对超时",
        "下发 bridge.open 后等待设备建立隧道的时间；超时即作废本次会话，不留待配对状态",
        minimum=5,
        maximum=300,
        unit="秒",
    ),
    # ── 自动结算（服务端侧）──
    ConfigSpec(
        "auto_settle_enabled",
        BOOL,
        SERVER,
        False,
        "启用自动结算",
        "占用时长超过阈值的会话由服务端自动结算",
    ),
    ConfigSpec(
        "auto_settle_threshold_s",
        INT,
        SERVER,
        1140,
        "自动结算阈值",
        "占用超过该时长即自动结算（默认 19 分钟）",
        minimum=60,
        maximum=7200,
        unit="秒",
    ),
    ConfigSpec(
        "auto_settle_students",
        TEXT_MAP,
        SERVER,
        {},
        # 模块级单例，全进程仅此一份；defaults() 会为调用方复制，勿在此就地修改
        "自动结算名单",
        "学号到密码的映射；只对这些学号执行自动结算，留空表示全部",
    ),
)

#: 键 → 规格，供 O(1) 查询。
SPECS_BY_KEY: dict[str, ConfigSpec] = {spec.key: spec for spec in CONFIG_SPECS}

#: 下发给设备的键（其默认值也是服务端侧合并的起点）。
DEVICE_KEYS: frozenset[str] = frozenset(spec.key for spec in CONFIG_SPECS if spec.scope is DEVICE)

#: 仅服务端使用的键。
SERVER_KEYS: frozenset[str] = frozenset(spec.key for spec in CONFIG_SPECS if spec.scope is SERVER)


def defaults() -> dict[str, int | bool | str | dict[str, str]]:
    """全部键的默认值。

    `auto_settle_students` 的默认值是可变对象，每次调用返回新副本，
    避免调用方改动它而污染登记表。
    """
    return {spec.key: ({} if spec.kind is TEXT_MAP else spec.default) for spec in CONFIG_SPECS}
