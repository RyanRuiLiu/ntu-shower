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
    `group` 是界面分组（配置页按它分卡片），取值见 [GROUPS]；分组又归属
    `SECTIONS` 之一（基础能力 / 拓展功能）。
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
    group: str = "misc"


@dataclass(frozen=True)
class GroupSpec:
    """一个界面分组的规格：键名、标题与所属分区。"""

    key: str
    title: str
    section: str


#: 分区 registry：配置页的两个大区，**业务功能在前**。**业务功能**是这个软件
#: 存在的目的（自动结算这类自建业务扩展，需要按需及时调整）；**运行基础**
#: 是支撑它长期跑着的地基（链路、采集、自动化、界面、遮罩、守护、自更新），
#: 装好后基本不动。
SECTIONS: tuple[tuple[str, str], ...] = (
    ("business", "业务功能"),
    ("infra", "运行基础"),
)

#: 界面分组 registry：顺序即配置页卡片顺序。分组元数据只在这里一份，
#: 前端据此分卡片渲染，不在各处硬编码键表。
GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec("settle", "自动结算", "business"),
    GroupSpec("gateway", "链路与心跳", "infra"),
    GroupSpec("doors", "喷头状态采集", "infra"),
    GroupSpec("automation", "自动化流程", "infra"),
    GroupSpec("ui", "界面操作", "infra"),
    GroupSpec("overlay", "操作遮罩", "infra"),
    GroupSpec("guard", "崩溃守护", "infra"),
    GroupSpec("ota", "自更新", "infra"),
    GroupSpec("debug", "调试通道", "business"),
    GroupSpec("misc", "其他", "infra"),
)


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
        "设备向服务端上报存活的时间间隔。调大它省电省流量，但服务端判定设备离线会更迟"
        "（离线判据依赖心跳与 ping 双通道）；调小则链路更灵敏、开销更高。30 秒是真机"
        "长期验证的平衡点",
        minimum=5,
        maximum=600,
        unit="秒",
        group="gateway",
    ),
    ConfigSpec(
        "ping_interval_s",
        INT,
        SERVER,
        60,
        "服务端探测间隔",
        "服务端定时问设备「现在几点」，用往返时间算出设备的钟快了还是慢了几分钟——"
        "设备没有联网对时，喷头的使用时间全靠这个校正。只影响时间显示的准确性，"
        "不影响控制功能",
        minimum=10,
        maximum=1800,
        unit="秒",
        group="gateway",
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
        group="gateway",
    ),
    ConfigSpec(
        "bridge_ssh_target",
        TEXT,
        SERVER,
        "root@iseekdream.com",
        "调试通道的 SSH 目标",
        "网页端生成本机连接命令时用的 `用户@主机`。它是**现场那台电脑**要 ssh 过去的地方，"
        "与网页自己的域名不是一回事——网页挂在 ntu-shower 子域上，而 ssh 连的是主域，"
        "所以推不出来，只能配。见 docs/RUNBOOK.md 的连接指引。",
        group="debug",
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
        group="gateway",
    ),
    ConfigSpec(
        "reconnect_max_ms",
        INT,
        DEVICE,
        60000,
        "重连退避上限",
        "断线重连等待的封顶值，避免无限增长。与退避基准配套：5 秒起步、60 秒封顶意味着"
        "连续失败五六次后就稳定在每分钟试一次——网络恢复后最多一分钟内自动回来，"
        "又不会在网络长时间中断时打爆服务器",
        minimum=5000,
        maximum=600000,
        unit="毫秒",
        group="gateway",
    ),
    ConfigSpec(
        "watchdog_interval_s",
        INT,
        DEVICE,
        60,
        "看门狗间隔",
        "设备每隔这么久自己检查一次服务还活着没有，死了就重新拉起（活着就无事发生）。"
        "这是设备能长期无人值守的关键一环。60 秒意味着最坏情况下一分钟内自愈",
        minimum=30,
        maximum=3600,
        unit="秒",
        group="gateway",
    ),
    # ── 喷头状态采集 ──
    ConfigSpec(
        "door_report_debounce_ms",
        INT,
        DEVICE,
        1000,
        "上报去抖窗口",
        "喷头状态变化后先等这么久再上报，把一小段时间里的连续变化合并成一条。"
        "调大会让状态显示变迟钝，调小可能产生碎片数据；1 秒是实测的合理折中",
        minimum=0,
        maximum=60000,
        unit="毫秒",
        group="doors",
    ),
    ConfigSpec(
        "outbox_max_rows",
        INT,
        DEVICE,
        100_000,
        "发件箱行数上限",
        "断网期间，状态变化先存在设备上（发件箱），网络恢复后补发。这是存多少条的上限，"
        "超出丢最旧的。默认十万条刻意给得远超所需（约 100MB，设备可用空间 11.9GB 的"
        "百分之一都不到），正常永远不会触发。**调小等于主动丢弃断网期间的历史**",
        minimum=1000,
        maximum=1_000_000,
        unit="行",
        group="doors",
    ),
    # ── 自动化遮盖 ──
    ConfigSpec(
        "overlay_enabled",
        BOOL,
        DEVICE,
        True,
        "显示操作遮罩",
        "自动操作期间是否盖住屏幕，防止现场学生误触",
        group="overlay",
    ),
    ConfigSpec(
        "overlay_message",
        TEXT,
        DEVICE,
        "网络连接中，请稍后重试...",
        "操作遮罩文字",
        "盖屏期间显示的文字。刻意**不写「正在自动操作」**——现场学生看到网络问题会安静等待，"
        "看到「系统正在远程操作这台机器」则可能去拔电源或找来管理员。这句话沿用旧项目",
        group="overlay",
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
        group="overlay",
    ),
    ConfigSpec(
        "overlay_text_color",
        TEXT,
        DEVICE,
        "#d94f16",
        "遮罩文字颜色",
        "十六进制颜色，如 #d94f16。默认值来自旧项目，与浴室 App 的提示色一致",
        group="overlay",
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
        group="overlay",
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
        group="automation",
    ),
    # ── 命令去重 ──
    ConfigSpec(
        "command_dedup_max_count",
        INT,
        DEVICE,
        1024,
        "命令去重窗口",
        "设备记住最近多少条命令的结果，用来认出「同一条命令发来了两次」。"
        "分配和结算是真的会操作硬件的，重复执行就是重复扣钱，所以宁可多记不能少记。"
        "默认 1024 条（约 200KB 内存）远比够用大",
        minimum=16,
        maximum=65536,
        unit="条",
        group="automation",
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
        group="automation",
    ),
    ConfigSpec(
        "cert_verify_wait_ms",
        INT,
        DEVICE,
        5000,
        "等待身份校验",
        "自动化流程提交学号密码后，等待浴室 App 弹出校验结果的时长。调大只会在"
        "「校验确实失败」的场景多等（流程有明确的结果判定，不会盲目等满）；调小会让"
        "慢网络下的正常校验被误判为超时。5 秒是真机多次实测的余量",
        minimum=500,
        maximum=30000,
        unit="毫秒",
        group="automation",
    ),
    ConfigSpec(
        "home_timeout_ms",
        INT,
        DEVICE,
        10000,
        "回首页预算",
        "自动化流程每一步之间都要先回到浴室 App 首页，这是单次返回的时间预算。"
        "正常返回在 1 秒内完成；预算给到 10 秒是留给设备卡顿时的余量——超预算会判定"
        "流程卡死并中止，调小会误杀正在缓慢恢复的界面",
        minimum=1000,
        maximum=60000,
        unit="毫秒",
        group="automation",
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
        group="ui",
    ),
    ConfigSpec(
        "ui_click_delay_ms",
        INT,
        DEVICE,
        200,
        "点击后延迟",
        "每次点击后的固定等待。0 是合法值：界面的响应判定是事件驱动的（页面一变就继续），"
        "这个延迟只是给不触发页面变化的点击（如选中态切换）留的缓冲。200 毫秒来自旧项目"
        "真机验证",
        minimum=0,
        maximum=5000,
        unit="毫秒",
        group="ui",
    ),
    ConfigSpec(
        "ui_dump_max_nodes",
        INT,
        DEVICE,
        2000,
        "节点导出上限",
        "「界面检视」读取设备画面控件列表时的数量上限，超出部分不返回。"
        "调大能看全复杂界面，但传输变慢；2000 个对浴室 App 的界面绰绰有余",
        minimum=100,
        maximum=20000,
        unit="个",
        group="ui",
    ),
    ConfigSpec(
        "ui_operation_timeout_s",
        INT,
        SERVER,
        30,
        "界面操作超时",
        "在网页上做点击/按键/输入后，最多等这么久要结果；设备没响应时你等的就是这个时长。30 秒覆盖了设备卡顿时的慢响应",
        minimum=5,
        maximum=300,
        unit="秒",
        group="ui",
    ),
    ConfigSpec(
        "ui_swipe_duration_ms",
        INT,
        DEVICE,
        300,
        "滑动默认时长",
        "网页上做滑动操作时，从起点滑到终点的持续时间。太短会被设备当成「用力一甩」"
        "（惯性滚动），太长则滑动显得迟缓；300 毫秒是正常拖动的手感",
        minimum=50,
        maximum=5000,
        unit="毫秒",
        group="ui",
    ),
    ConfigSpec(
        "device_exec_max_output_bytes",
        INT,
        DEVICE,
        32768,
        "命令输出上限",
        "远程执行命令后，结果最多带回这么多字节，超出部分不显示。"
        "调大能看更长输出，但占用更多设备内存与传输时间；32KB 够看绝大多数命令结果",
        minimum=1024,
        maximum=1048576,
        unit="字节",
        group="gateway",
    ),
    # ── 自更新 ──
    ConfigSpec(
        "ota_check_interval_s",
        INT,
        DEVICE,
        3600,
        "检查更新间隔",
        "设备主动查询 /ota/manifest 的时间间隔。在线设备另有发布即时推送，这个轮询是"
        "离线设备的补救通道——离线时错过的发布靠它补装。1 小时一次在「升级尽快生效」"
        "与「查询开销」之间平衡；配合发布推送，实际升级延迟通常远小于它",
        minimum=60,
        maximum=86400,
        unit="秒",
        group="ota",
    ),
    # ── 浴室 App 崩溃守护 ──
    ConfigSpec(
        "bath_app_guard_enabled",
        BOOL,
        DEVICE,
        True,
        "崩溃后拉起浴室 App",
        "浴室 App 异常退出时是否自动重新拉起",
        group="guard",
    ),
    ConfigSpec(
        "bath_app_exit_window_ms",
        INT,
        DEVICE,
        30000,
        "退出计数窗口",
        "在这个窗口内退出次数达到上限才判定为崩溃（单次退出可能只是用户按了主页）。"
        "30 秒窗口 + 3 次阈值能区分「用户主动退出」与「App 反复崩溃」——后者才值得"
        "自动拉起，前者拉起反而是打扰",
        minimum=1000,
        maximum=600000,
        unit="毫秒",
        group="guard",
    ),
    ConfigSpec(
        "bath_app_exit_max_count",
        INT,
        DEVICE,
        3,
        "窗口内最多退出次数",
        "在「退出计数窗口」内退出达到这个次数，就认定浴室 App 在反复崩溃，"
        "自动拉起后会进入冷却期；默认 3 次已能区分误触主页与真崩溃",
        minimum=1,
        maximum=100,
        unit="次",
        group="guard",
    ),
    ConfigSpec(
        "bath_app_exit_cooldown_ms",
        INT,
        DEVICE,
        600000,
        "异常后的冷却时长",
        "确认浴室 App 反复崩溃后，暂停自动拉起这么久，给它自己恢复的机会——"
        "持续强行拉起一个正在崩溃的 App 只会雪上加霜。10 分钟后若再崩溃会重新计数",
        minimum=1000,
        maximum=3600000,
        unit="毫秒",
        group="guard",
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
        group="debug",
    ),
    ConfigSpec(
        "bridge_pair_timeout_s",
        INT,
        SERVER,
        30,
        "调试通道配对超时",
        "点「开桥」后，设备建立隧道最多给这么长时间，超时就当这次没成功，"
        "需要重新点开桥。30 秒覆盖设备启动 adb 服务的正常耗时",
        minimum=5,
        maximum=300,
        unit="秒",
        group="debug",
    ),
    # ── 自动结算（服务端侧）──
    ConfigSpec(
        "auto_settle_enabled",
        BOOL,
        SERVER,
        False,
        "启用自动结算",
        "自动结算的总开关（默认关）。开启后，占用时长超过「自动结算阈值」的会话由服务端"
        "自动代为结算，防止学生忘了结算导致喷头整夜占用。名单（auto_settle_students）"
        "非空时只对名单内的学号生效",
        group="settle",
    ),
    ConfigSpec(
        "auto_settle_threshold_s",
        INT,
        SERVER,
        1140,
        "自动结算阈值",
        "占用持续超过这个时长（默认 19 分钟）还没结算，服务端就代为结算。"
        "调小回收更快但可能误伤还在冲洗的人；调大则喷头可能被长时间占用",
        minimum=60,
        maximum=7200,
        unit="秒",
        group="settle",
    ),
    ConfigSpec(
        "auto_settle_students",
        TEXT_MAP,
        SERVER,
        {},
        # 模块级单例，全进程仅此一份；defaults() 会为调用方复制，勿在此就地修改
        "自动结算名单",
        "只对这些学号做自动结算，一行一个（格式：学号=密码）。留空表示所有人；"
        "密码用于调用浴室系统的结算接口，仅保存在服务端数据库",
        group="settle",
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
