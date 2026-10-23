"""远程配置的规格登记表：每个键的类型、范围、归属与说明。

"有哪些键、各自什么类型与范围、归谁用"在此登记为可被程序读取的事实：服务端
据此校验入参，客户端据此渲染控件，前端不会另抄一份键表。

scope=DEVICE 的键下发给设备，设备端 RuntimeConfig 是它的消费者；两边的键集合
由 tests/test_config_spec.py 对账，防止单边漂移。
"""

import re
from collections.abc import Iterable
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
    """字符串到字符串的映射，目前只有三份后台任务名单一例（学号=密码）。"""

    TIME = "time"
    """一天里的一个时刻，`HH:MM`。时刻不是时长、也没有单位：`23:49` 与 `1429 分钟` 是
    同一件事，而后者要读的人自己换算。下限与上限用自 0 点起的分钟数表达。"""

    WEEKDAYS = "weekdays"
    """星期集合，值形如 `1,2,3,4,5`，1=周一……7=周日。空集合不合法。
    与 [TIME] 一样，识别与规范化的全部知识都在 [parse_weekdays] / [format_weekdays]
    里，校验、默认值与测试都从这一份派生。"""


#: `HH:MM` 形式的一天内时刻，小时 00–23、分钟 00–59。时刻这个类型的全部知识都在下面
#: 这两个函数里——怎么认它、怎么让它与分钟数互相换算。校验（[spec.py] 的范围）与测试
#: （默认值在不在范围内）都要用它，写第二份的地方必然与第一份漂移。
_TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

#: 星期值的唯一形状：ASCII `1`–`7`。ISO 编号，周一=1、周日=7。
_WEEKDAY_PATTERN = re.compile(r"[1-7]")


def parse_time(value: str) -> int | None:
    """`HH:MM` → 自 0 点起的分钟数；格式不对给 None。"""
    matched = _TIME_PATTERN.fullmatch(value)
    if matched is None:
        return None
    return int(matched.group(1)) * 60 + int(matched.group(2))


def format_time(minutes: int) -> str:
    """自 0 点起的分钟数 → `HH:MM`，用于把范围错误说得像人话。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_weekdays(value: str) -> tuple[int, ...] | None:
    """`1,2,5` → `(1,2,5)`；空串、空项、越界或非数字给 None。

    去重并升序：`3,1,3` 与 `1,3` 是同一个集合，存进配置的永远是规范形式。
    """
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        return None
    days: set[int] = set()
    for part in parts:
        if _WEEKDAY_PATTERN.fullmatch(part) is None:
            return None
        days.add(int(part))
    return tuple(sorted(days))


def format_weekdays(days: Iterable[int]) -> str:
    """星期集合 → 规范形式 `1,2,3`。"""
    return ",".join(str(day) for day in sorted(set(days)))


class ConfigScope(str, Enum):
    """归属：键由哪一端消费。"""

    DEVICE = "device"
    """下发给设备，由设备端 RuntimeConfig 应用。"""

    SERVER = "server"
    """仅服务端自己使用，不下发给设备。"""


@dataclass(frozen=True)
class ConfigSpec:
    """一个配置键的完整规格。

    `minimum` / `maximum` 只对 `ConfigKind.INT` 与 `ConfigKind.TIME` 有意义——后者用自
    0 点起的分钟数表达；其余类型为 None。
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


#: 分区清单：配置页的两个页签，**按"这个键去哪"分**，不按主题分。
#: 服务端那一边在服务器上运行——不下发设备、也不按设备区分；设备那一边会推到设备、
#: 支持单台覆盖。两者的边界就是 [ConfigScope]：**一组里的键必须同属一个 scope**，
#: 由 `tests/test_config_spec.py` 强制，不允许一组混两种。
#: 服务端在前：后台任务是这个软件存在的目的，设备侧的九块是支撑它长期跑着的地基。
SECTIONS: tuple[tuple[str, str], ...] = (
    ("server", "服务端业务"),
    ("device", "设备下发"),
)

#: 界面分组清单：顺序即配置页卡片顺序（设备侧那几块按改动的频繁程度排——遮罩实测常改，
#: 链路、守护、自更新是一次性调好的）。
#: **从原来按主题分的组里拆出来的服务端那几块换了组名**（`server_link` / `server_ui` /
#: `server_bridge` / `server_clock`），不与设备侧的同名组混在一起——同一个名字只能指一件事。
#: 分组元数据只在这里一份，前端据此分卡片渲染，不在各处硬编码键表。
GROUPS: tuple[GroupSpec, ...] = (
    # ── 服务端业务：在服务器上运行，不下发设备 ──
    GroupSpec("renew_wait", "预约续期", "server"),
    GroupSpec("renew_session", "会话续期", "server"),
    GroupSpec("auto_settle", "自动结算", "server"),
    GroupSpec("extend_hours", "延长服务时间", "server"),
    GroupSpec("server_clock", "自动校时", "server"),
    GroupSpec("server_link", "链路维持", "server"),
    GroupSpec("server_ui", "界面操作超时", "server"),
    GroupSpec("server_bridge", "调试通道", "server"),
    GroupSpec("server_voice", "语音播报", "server"),
    # ── 设备下发：会推到设备，支持单台覆盖 ──
    GroupSpec("power", "电源计划", "device"),
    GroupSpec("overlay", "操作遮罩", "device"),
    GroupSpec("notice", "屏幕提示", "device"),
    GroupSpec("notice_condition", "提示条件", "device"),
    GroupSpec("doors", "喷头状态采集", "device"),
    GroupSpec("ui", "界面操作", "device"),
    GroupSpec("automation", "自动化流程", "device"),
    GroupSpec("bath_db", "浴室 App 数据库", "device"),
    GroupSpec("gateway", "链路与心跳", "device"),
    GroupSpec("guard", "崩溃守护", "device"),
    GroupSpec("ota", "自更新", "device"),
    GroupSpec("debug", "调试通道", "device"),
)


DEVICE = ConfigScope.DEVICE
SERVER = ConfigScope.SERVER
INT = ConfigKind.INT
BOOL = ConfigKind.BOOL
TEXT = ConfigKind.TEXT
TEXT_MAP = ConfigKind.TEXT_MAP
TIME = ConfigKind.TIME
WEEKDAYS = ConfigKind.WEEKDAYS

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
        "服务端按此间隔向设备发一次 link.ping：既确认链路还活着（一个周期内收不到 pong "
        "就断开），也把服务端此刻的时刻交给设备——帧的产生时刻由设备侧的时间锚点推"
        "出，而锚点正是从这条 ping 上取的，越新越准",
        minimum=10,
        maximum=1800,
        unit="秒",
        group="server_link",
    ),
    ConfigSpec(
        "clock_jump_threshold_s",
        INT,
        SERVER,
        60,
        "设备钟跳变阈值",
        "服务端把设备回 pong 时的墙上时刻与收到时刻相减，得到两个钟的偏差，再拿它把帧"
        "里的 open_time / in_time 平移到服务端时间轴——那两个字段是「这次会话从什么时候"
        "开始」的唯一线索，而设备钟的绝对值不可信（自然漂移 8.5 秒/天，还会被人为回拨"
        "一小时）。漂移很慢所以取 EMA 平滑滤掉单次往返的抖动；而设备重启、被手动改钟这"
        "类跳变是几十分钟级的，超过这个阈值就直接采信新值，继续平滑会让之后几次映射都按"
        "错误的偏差来算。",
        minimum=5,
        maximum=3600,
        unit="秒",
        group="server_link",
    ),
    ConfigSpec(
        "reconnect_base_ms",
        INT,
        DEVICE,
        5000,
        "重连退避基准",
        "断线后首次重连等待的上限，随后按指数退避；各级实际等待带一半的随机抖动——"
        "几十台设备共用一次断网时，同步的退避会变成同一波次的同时重连，抖动把它们摊开",
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
        "连续失败五六次后就稳定在每半分钟到一分钟试一次——网络恢复后最多一分钟内自动回来，"
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
        "百分之一都不到），正常永远不会触发。调小等于主动丢弃断网期间的历史",
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
        "自动操作期间盖住屏幕，防止现场学生误触",
        group="overlay",
    ),
    ConfigSpec(
        "overlay_message",
        TEXT,
        DEVICE,
        "网络连接中，请稍后重试...",
        "操作遮罩文字",
        "盖屏期间显示的文字。刻意不写「正在自动操作」——现场学生看到网络问题会安静等待，"
        "看到「系统正在远程操作这台机器」则可能去拔电源或找来管理员",
        group="overlay",
    ),
    ConfigSpec(
        "overlay_text_size_sp",
        INT,
        DEVICE,
        80,
        "遮罩文字字号",
        "单位为 sp。设备屏幕为 1366x768 横屏，字号小了看不清",
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
        "十六进制颜色，如 #d94f16；默认值与浴室 App 的提示色一致",
        group="overlay",
    ),
    ConfigSpec(
        "overlay_margin_left_dp",
        INT,
        DEVICE,
        600,
        "遮罩文字左边距",
        "单位为 dp。文字落位在提示图的右侧空白处",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="overlay",
    ),
    # ── 屏幕提示 ──
    # 配置面在服务端，显隐判定在设备端：服务端只下发文字/样式/条件，设备用服务端
    # 时间轴与本地 t_doorlist 判断此刻是否显示。边界与理由见 docs/DECISIONS.md。
    ConfigSpec(
        "notice_enabled",
        BOOL,
        DEVICE,
        False,
        "显示屏幕提示",
        "开启后，若提示文字非空且显示条件满足，就在浴室屏幕上显示一行字。默认关闭；关闭时不创建提示窗",
        group="notice",
    ),
    ConfigSpec(
        "notice_text",
        TEXT,
        DEVICE,
        "",
        "提示文字",
        "要显示的一行文字。为空时即使开关打开也不显示。换行会被替换为空格；超出屏幕右缘的部分以省略号截断",
        group="notice",
    ),
    ConfigSpec(
        "notice_text_size_sp",
        INT,
        DEVICE,
        48,
        "提示字号",
        "单位为 sp，跟随系统字体缩放。设备屏幕为 1366×768 横屏；默认 48sp 是操作遮罩 80sp 的六成，保证一米左右可读",
        minimum=10,
        maximum=200,
        unit="sp",
        group="notice",
    ),
    ConfigSpec(
        "notice_text_color",
        TEXT,
        DEVICE,
        "#d94f16",
        "提示文字颜色",
        "十六进制颜色，如 #d94f16；也可写 #AARRGGBB 带透明度。格式无法识别时显示白色，并在设备日志留痕",
        group="notice",
    ),
    ConfigSpec(
        "notice_x_dp",
        INT,
        DEVICE,
        40,
        "提示左边距",
        "文字左边缘距屏幕左边缘的距离，单位为 dp。屏幕固定 1366×768；填到超过屏幕宽度的值会让文字落到屏幕外",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="notice",
    ),
    ConfigSpec(
        "notice_y_dp",
        INT,
        DEVICE,
        40,
        "提示上边距",
        "文字上边缘距屏幕顶部的距离，单位为 dp。屏幕固定 1366×768；填到超过屏幕高度的值会让文字落到屏幕外",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="notice",
    ),
    # ── 提示条件 ──
    ConfigSpec(
        "notice_time_enabled",
        BOOL,
        DEVICE,
        False,
        "启用时间条件",
        "开启后，只有设备当前的服务端时间落在下面的星期与时段内才显示。服务端时间尚无锚点"
        "时（刚启动且未连上服务端）不显示；设备离线但已有锚点时，仍按单调钟继续走时。"
        "时间按设备系统时区换算，与浴室 App 的日历一致",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_time_start",
        TIME,
        DEVICE,
        "22:00",
        "提示时段开始",
        "每天的开始时刻，含这一刻。开始晚于结束时视为跨午夜（如 23:30–00:30）；开始等于"
        "结束表示全天。延长服务期间设备表盘可能被拨快跨日，而本条件按真实的服务端时间"
        "判断，两者会不一致",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_time_end",
        TIME,
        DEVICE,
        "23:00",
        "提示时段结束",
        "每天的结束时刻，不含这一刻。与开始时刻相同表示全天",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_weekdays",
        WEEKDAYS,
        DEVICE,
        "1,2,3,4,5,6,7",
        "提示生效星期",
        "只在选中的星期生效。1=周一……7=周日。跨午夜的时段按开始那一天归属：例如周五 "
        "23:30–00:30 会覆盖周六 00:00–00:30，即使周六未勾选",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_occupancy_enabled",
        BOOL,
        DEVICE,
        False,
        "启用占用条件",
        "开启后，只有当前占用的喷头数落在下面的区间内才显示。占用指等待开阀、淋浴中、"
        "已暂停三种状态；维修中与已禁用不算。读不到完整的喷头读数时不显示",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_occupancy_min",
        INT,
        DEVICE,
        0,
        "占用喷头数下限",
        "包含下限。与上限相同可以精确匹配一个数字；下限大于上限时提示不会显示",
        minimum=0,
        maximum=48,
        unit="个",
        group="notice_condition",
    ),
    ConfigSpec(
        "notice_occupancy_max",
        INT,
        DEVICE,
        48,
        "占用喷头数上限",
        "包含上限。最大 48 与协议里的喷头号上限一致；0 表示只在没有喷头被占用时显示",
        minimum=0,
        maximum=48,
        unit="个",
        group="notice_condition",
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
        15000,
        "回首页预算",
        "自动化流程每一步之间都要先回到浴室 App 首页，这是单次返回的时间预算。"
        "正常返回在 1 秒内完成；预算给到 15 秒是留给两种情形的余量：设备卡顿，以及"
        "**浴室 App 刚被重启过**——那时它还在冷启动，而流程的第一步就是回首页，"
        "10 秒会在它露脸之前就判定流程卡死。超预算会中止并判失败，调小会误杀"
        "正在缓慢恢复的界面",
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
        "「变化通知没来」时生效——所以它是最坏情况的上限，不是固定开销。"
        "60 毫秒对浴室 App 的刷新频率够用",
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
        "这个延迟只是给不触发页面变化的点击（如选中态切换）留的缓冲",
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
        group="server_ui",
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
        group="debug",
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
    # ── 浴室 App 数据库 ──
    ConfigSpec(
        "bath_db_archive_enabled",
        BOOL,
        DEVICE,
        False,
        "启用数据库归档",
        "浴室 App 的 bath.db 随会话只增不减，涨大后 App 明显卡顿。开启后，本程序每次"
        "启动时检查库大小，攒到阈值即把它改名归档（**永久保留**，归档文件不带自动"
        "清理），浴室 App 下次打开原路径时自动建一个全新的空库。改名对运行中的 App "
        "无害（它持有的文件句柄跟着走，实测无感知）；每晚断电重启，归档后第二天一定"
        "是新库。默认关闭",
        group="bath_db",
    ),
    ConfigSpec(
        "bath_db_archive_threshold_mb",
        INT,
        DEVICE,
        10,
        "归档触发阈值",
        "bath.db 攒到这个大小，本程序启动时就把它归档。设得小归档勤（新库更频繁，"
        "历史库文件多），设得大归档懒（App 长期跑在一个大库上）。实测 8MB 约为三个月"
        "的会话量",
        minimum=5,
        maximum=1024,
        unit="MB",
        group="bath_db",
    ),
    # ── 调试通道 ──
    ConfigSpec(
        "bridge_max_lifetime_s",
        INT,
        SERVER,
        3600,
        "调试通道最长开启时长",
        "从打开算起的最长存活时长，到点强制关闭。这是唯一的自动关闭条件：设备侧隧道连入"
        "之后、本侧执行 `adb connect` 之前一个字节都不会发，空闲超时会误关正在使用的桥。"
        "调试的节奏由人定，中途停下来想事情、去装 adb 都正常，不该被打断",
        minimum=300,
        maximum=86400,
        unit="秒",
        group="server_bridge",
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
        group="server_bridge",
    ),
    ConfigSpec(
        "bridge_ssh_target",
        TEXT,
        SERVER,
        "deploy@<deploy-host>",
        "调试通道的 SSH 目标",
        "网页端生成本机连接命令时用的 `用户@主机`。它是现场那台电脑要 ssh 过去的地方，"
        "与网页自己的域名不是一回事——网页挂在 ntu-shower 子域上，而 ssh 连的是主域，"
        "所以推不出来，只能配。见 docs/RUNBOOK.md 的连接指引。",
        group="server_bridge",
    ),
    ConfigSpec(
        "voice_event_max_age_s",
        INT,
        SERVER,
        60,
        "语音播报事件时效",
        "帧的采集时刻距服务端处理时刻超过这个秒数就不播报：设备离线或链路积压后补上来的"
        "旧事件不补播。0 表示不限制。它只影响语音，不改变帧派生、会话与统计",
        minimum=0,
        maximum=3600,
        unit="秒",
        group="server_voice",
    ),
    # ── 后台任务（服务端侧）：一个功能一块，块内按"开关 → 阈值 → 名单"排 ──
    ConfigSpec(
        "renew_wait_enabled",
        BOOL,
        SERVER,
        False,
        "启用预约续期",
        "名单内学号的喷头完成分配后若一直未开阀，浴室 App 会在无水释放门槛到达时释放该喷头"
        "并结束本场会话。开启后，服务端在释放发生前结算并重新分配同一喷头，使这次分配得以保留；"
        "累计续期时长达到上限后不再续期",
        group="renew_wait",
    ),
    ConfigSpec(
        "renew_wait_threshold_s",
        INT,
        SERVER,
        270,
        "预约续期阈值",
        "从分配时刻起算，过了这个时长还没开阀就续一次。上界是浴室 App 那条 300 秒的无水"
        "释放门槛——设到 300 或更大，浴室端会先把它放掉，我们就不是续、而是重新开一场",
        minimum=60,
        maximum=300,
        unit="秒",
        group="renew_wait",
    ),
    ConfigSpec(
        "renew_wait_max_duration_s",
        INT,
        SERVER,
        1200,
        "预约续期时长上限",
        "从这一串的第一场（他自己那一次分配）算起，累计替他续了这么久就停，让他被浴室端"
        "正常释放——他从没出过水，没有账要结。喷头空着超过 300 秒即算这一串结束、计时从头"
        "开始。浴室端每 300 秒放一次、我们每 270 秒续一次，所以 1200 秒约合四场",
        minimum=0,
        unit="秒",
        group="renew_wait",
    ),
    ConfigSpec(
        "renew_wait_students",
        TEXT_MAP,
        SERVER,
        {},  # 可变默认值：defaults() 每次返回新副本，勿在此就地修改
        "预约续期名单",
        "只对这些学号做预约续期，一行一个（格式：学号=密码）。留空表示谁都不做。"
        "**凭证跟着这份名单走**：同一个学号要开两个功能，就在那两份名单里各写一遍——"
        "三份名单各自完整、互不依赖，配一份就读得懂一份",
        group="renew_wait",
    ),
    ConfigSpec(
        "renew_session_enabled",
        BOOL,
        SERVER,
        False,
        "启用会话续期",
        "名单内学号的会话处于淋浴中、且时长逼近浴室 App 的强制释放上限时，该场会话将被结束。"
        "开启后，服务端在分配满续期阈值时结算并重新分配同一喷头，使本次淋浴得以继续",
        group="renew_session",
    ),
    ConfigSpec(
        "renew_session_threshold_s",
        INT,
        SERVER,
        1140,
        "会话续期阈值",
        "从分配时刻起算，过了这个时长就续一次（默认 1140 秒＝19 分钟）。上界 1200 是浴室 App"
        "的强制释放点，留出的余量要盖住两条命令的本事（实测 5–10 秒）**再加上排队**——同一"
        "台设备上的续期是一条接一条跑的，同校同时到点的人越多，最后一个动手越晚",
        minimum=300,
        maximum=1200,
        unit="秒",
        group="renew_session",
    ),
    ConfigSpec(
        "renew_session_students",
        TEXT_MAP,
        SERVER,
        {},
        "会话续期名单",
        "只对这些学号做会话续期，一行一个（格式：学号=密码）。留空表示谁都不做。"
        "与预约续期各自独立——同一个人可以只开其中一个",
        group="renew_session",
    ),
    ConfigSpec(
        "auto_settle_enabled",
        BOOL,
        SERVER,
        False,
        "启用自动结算",
        "名单内学号的会话关阀后若长时间未再开阀，该喷头将持续处于占用状态。开启后，服务端"
        "在关阀满结算阈值时代为结算，使该喷头得以释放",
        group="auto_settle",
    ),
    ConfigSpec(
        "auto_settle_pause_threshold_s",
        INT,
        SERVER,
        60,
        "自动结算阈值",
        "已暂停持续超过这个时长就结算一次。暂停时阀已经关着，所以这一步不会打断正在淋浴的"
        "人；它结束的是这一场会话——他要是回来开阀，需要重新分配。按实测，暂停后还会回来"
        "的人里有 15.2% 会在 60 秒之后才回来，调大它可以少打断一些，代价是喷头被占得久些",
        minimum=15,
        maximum=300,
        unit="秒",
        group="auto_settle",
    ),
    ConfigSpec(
        "auto_settle_students",
        TEXT_MAP,
        SERVER,
        {},
        "自动结算名单",
        "只对这些学号做自动结算，一行一个（格式：学号=密码）。留空表示谁都不做",
        group="auto_settle",
    ),
    ConfigSpec(
        "extend_hours_enabled",
        BOOL,
        SERVER,
        False,
        "启用延长服务时间",
        "浴室 App 每日按时关机，并在关机前释放全部喷头。开启后，服务端在触发时刻先重布"
        "MCU 定时开关机（跳过当晚断电），再将设备时钟前移一段可配置的时间并清空喷头，"
        "把名单内学号重新分配回原喷头；喷头全部空闲且过了午夜后，设备时钟恢复原值",
        group="extend_hours",
    ),
    ConfigSpec(
        "extend_hours_at",
        TIME,
        SERVER,
        "23:49",
        "触发时刻",
        "设备表盘上的哪一刻动手。**触发时刻 + 拨快幅度必须跨过午夜**：这样才能一次跳过"
        "23:50 起的新人关闭窗口、23:58 的断电，以及两者之间的全部释放判定。浴室 App 实测"
        "在 23:53 动手放人（关机前五分钟），所以触发时刻要在这之前留足余量",
        minimum=23 * 60,
        group="extend_hours",
    ),
    ConfigSpec(
        "extend_shift_minutes",
        INT,
        SERVER,
        15,
        "拨快幅度",
        "设备表盘从触发时刻向前跳过的分钟数。落点必须跨过午夜（触发时刻 + 幅度 ≥ 00:00），"
        "这样才能一次跳过 23:50 起的新人关闭窗口、23:58 的断电，以及两者之间的全部释放判定。"
        "默认 15 分钟：23:49 拨快落在 00:04",
        minimum=10,
        maximum=120,
        unit="分钟",
        group="extend_hours",
    ),
    ConfigSpec(
        "extend_hours_zone_offset_minutes",
        INT,
        SERVER,
        480,
        "设备时区偏移",
        "设备墙钟与 UTC 的差，用来把最近一帧的读数换算成「设备表盘上现在几点」。默认 480 即"
        "UTC+8。**它是设备的事实而不是我们的策略**：浴室 App 判时间用的是设备默认时区，配错会"
        "让拨钟时机整体错位一小时，而且不报任何错",
        minimum=-720,
        maximum=840,
        unit="分钟",
        group="extend_hours",
    ),
    ConfigSpec(
        "extend_hours_students",
        TEXT_MAP,
        SERVER,
        {},
        "延长服务时间名单",
        "只在这些学号还有会话时才拨钟，一行一个（格式：学号=密码）。留空表示谁都不做。"
        "**凭证跟着这份名单走**：拨钟之后要替他们重新分配喷头，那一步要用密码——"
        "四份名单各自完整，配一份就读得懂一份",
        group="extend_hours",
    ),
    # ── 自动校时 ──
    ConfigSpec(
        "time_sync_enabled",
        BOOL,
        SERVER,
        False,
        "启用自动校时",
        "服务端每 30 秒检查一次设备钟偏差，超过阈值即把设备钟写准，并按电源计划重布 MCU。"
        "设备没有 NTP，时钟靠 RTC 维持、会跑偏（实测快过整 1 小时）；依赖开关机计划准确时"
        "应开启。三种情形自动让位：延长服务期间（偏差是故意的）、设备上有人占用（校钟的"
        "跳变会清掉会话）、还没有偏差记录（无从判）。设备页的「校正时间」是手动动作，"
        "不受此开关影响。默认关闭",
        group="server_clock",
    ),
    ConfigSpec(
        "time_sync_threshold_s",
        INT,
        SERVER,
        60,
        "校时触发阈值",
        "自动校时的门槛：设备时钟与服务端的偏差超过该秒数才执行校正并重布 MCU，"
        "偏差在阈值内不动。仅在「启用自动校时」开启时生效",
        minimum=60,
        unit="秒",
        group="server_clock",
    ),
    ConfigSpec(
        "power_off_at",
        TIME,
        DEVICE,
        "23:58",
        "每日断电时刻",
        "设备 MCU 每日定时断电的时刻（浴室 App 从 V8 平台拿到的是同一个值）。"
        "延长服务与自动校时都用它换算重布 MCU 的目标时刻；改它要与浴室平台那侧保持一致",
        group="power",
    ),
    ConfigSpec(
        "power_on_at",
        TIME,
        DEVICE,
        "10:55",
        "每日上电时刻",
        "设备 MCU 每日定时上电的时刻。重布 MCU 时与「每日断电时刻」成对使用：开机延时从断电时刻起算，到这个时刻上电",
        group="power",
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

    `renew_wait_students` 等三份名单的默认值是可变对象，每次调用返回新副本，
    避免调用方改动它而污染登记表。
    """
    return {spec.key: ({} if spec.kind is TEXT_MAP else spec.default) for spec in CONFIG_SPECS}
