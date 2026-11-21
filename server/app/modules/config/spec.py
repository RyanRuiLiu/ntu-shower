"""远程配置的规格登记表：每个键的类型、范围、归属与说明。

"有哪些键、各自什么类型与范围、归谁用"在此登记为可被程序读取的事实：服务端
据此校验入参，客户端据此渲染控件，前端不会另抄一份键表。

scope=DEVICE 的键下发给设备，设备端 RuntimeConfig 是它的消费者；两边的键集合
由 tests/test_config_spec.py 对账，防止单边漂移。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class ConfigKind(StrEnum):
    """取值类型。

    `StrEnum`（3.11+）：成员即字符串值，登记表下发与校验都按取值消费。
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
# `HH:MM` 的唯一出处：本模块 TIME 键与语音规则的生效时段共用同一形状。
# 锚定形态同时服务 re.fullmatch 与 Pydantic Field(pattern=)。
TIME_PATTERN = r"^([01]\d|2[0-3]):([0-5]\d)$"
_TIME_RE = re.compile(TIME_PATTERN)

#: 星期值的唯一形状：ASCII `1`–`7`。ISO 编号，周一=1、周日=7。
_WEEKDAY_PATTERN = re.compile(r"[1-7]")


def parse_time(value: str) -> int | None:
    """`HH:MM` → 自 0 点起的分钟数；格式不对给 None。"""
    matched = _TIME_RE.fullmatch(value)
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


class ConfigScope(StrEnum):
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
    choices: tuple[str, ...] | None = None
    """TEXT 的封闭取值集。界面据此渲染下拉，校验据此拒绝未知值；
    None 表示普通自由文本。"""


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
    ("server", "后台任务"),
    ("device", "设备下发"),
)

#: 界面分组清单：顺序即配置页卡片顺序。
#: 分组元数据只在这里一份，前端据此分卡片渲染，不在各处硬编码键表。
GROUPS: tuple[GroupSpec, ...] = (
    # ── 后台任务：在服务器上运行，不下发设备 ──
    GroupSpec("renew_wait", "预约续期", "server"),
    GroupSpec("renew_session", "会话续期", "server"),
    GroupSpec("auto_settle", "自动结算", "server"),
    GroupSpec("power", "延长服务时间", "server"),
    GroupSpec("server_clock", "自动校时", "server"),
    # ── 设备下发：会推到设备，支持单台覆盖 ──
    GroupSpec("notice", "屏幕提示", "device"),
    GroupSpec("notice_condition", "提示条件", "device"),
    GroupSpec("status_line", "楼层状态行", "device"),
    GroupSpec("status_line_condition", "状态行条件", "device"),
    GroupSpec("automation", "自动化流程", "device"),
    GroupSpec("bath_db", "浴室 App 数据库", "device"),
)


DEVICE = ConfigScope.DEVICE
SERVER = ConfigScope.SERVER
INT = ConfigKind.INT
BOOL = ConfigKind.BOOL
TEXT = ConfigKind.TEXT
TEXT_MAP = ConfigKind.TEXT_MAP
TIME = ConfigKind.TIME
WEEKDAYS = ConfigKind.WEEKDAYS

#: 跨模块引用的配置键名。以字符串字面量散落在消费方迟早漂移（登记表改名一处、
#: 消费方三处），跨模块引用一律取这里的常量。
HOLD_ENABLED_KEY = "hold_enabled"
HOLD_STUDENTS_KEY = "hold_students"
TIME_SYNC_ENABLED_KEY = "time_sync_enabled"


def _condition_specs(prefix: str, group: str, subject: str) -> list[ConfigSpec]:
    """两族窗口（屏幕提示 / 楼层状态行）共用的显示条件键：时间窗与占用区间。

    判定语义完全一致——同一套窗口模型、同一份占用口径（见 modules/status_line 与
    modules/notice 的判定实现）——差异只在键前缀、分组与说明里的主语。表驱动生成，
    改口径只动这一处。
    """
    return [
        ConfigSpec(
            f"{prefix}_time_enabled",
            BOOL,
            DEVICE,
            False,
            "启用时间条件",
            "开启后，只有设备当前的服务端时间落在下面的星期与时段内才显示"
            f"{subject}。服务端时间尚无锚点时（刚启动且未连上服务端）不显示；设备离线但已有"
            "锚点时，仍按单调钟继续走时。时间按设备系统时区换算，与浴室 App 的日历一致",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_time_start",
            TIME,
            DEVICE,
            "22:00",
            f"{subject}时段开始",
            "每天的开始时刻，含这一刻。开始晚于结束时视为跨午夜（如 23:30–00:30）；开始等于"
            "结束表示全天。延长服务期间设备表盘可能被拨快跨日，而本条件按真实的服务端时间"
            "判断，两者会不一致",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_time_end",
            TIME,
            DEVICE,
            "23:00",
            f"{subject}时段结束",
            "每天的结束时刻，不含这一刻。与开始时刻相同表示全天",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_weekdays",
            WEEKDAYS,
            DEVICE,
            "1,2,3,4,5,6,7",
            f"{subject}生效星期",
            "只在选中的星期生效。1=周一……7=周日。跨午夜的时段按开始那一天归属：例如周五 "
            "23:30–00:30 会覆盖周六 00:00–00:30，即使周六未勾选",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_occupancy_enabled",
            BOOL,
            DEVICE,
            False,
            "启用占用条件",
            "开启后，只有当前占用的喷头数落在下面的区间内才显示"
            f"{subject}。占用指等待开阀、淋浴中、已暂停三种状态；维修中与已禁用不算。"
            "读不到完整的喷头读数时不显示",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_occupancy_min",
            INT,
            DEVICE,
            0,
            "占用喷头数下限",
            f"包含下限。与上限相同可以精确匹配一个数字；下限大于上限时{subject}不会显示",
            minimum=0,
            maximum=48,
            unit="个",
            group=group,
        ),
        ConfigSpec(
            f"{prefix}_occupancy_max",
            INT,
            DEVICE,
            48,
            "占用喷头数上限",
            "包含上限。最大 48 与协议里的喷头号上限一致；0 表示只在没有喷头被占用时显示",
            minimum=0,
            maximum=48,
            unit="个",
            group=group,
        ),
    ]


#: 全部配置键的规格。新增配置键时只改这里——服务端校验、客户端渲染、文档
#: 生成都从它派生。
CONFIG_SPECS: tuple[ConfigSpec, ...] = (
    # ── 屏幕提示 ──
    # 配置面在服务端，显隐判定在设备端：服务端只下发文字/样式/条件，设备用服务端
    # 时间轴与本地 t_doorlist 判断此刻是否显示。边界与理由见 docs/adr/。
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
        24,
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
        "#de523f",
        "提示文字颜色",
        "十六进制颜色，如 #d94f16；也可写 #AARRGGBB 带透明度。格式无法识别时显示白色，并在设备日志留痕",
        group="notice",
    ),
    ConfigSpec(
        "notice_align",
        TEXT,
        DEVICE,
        "靠右",
        "提示水平对齐",
        "文字的基准位置：左对齐按左边距、靠右对齐按右边距、居中用居中偏移。上边距三种对齐都生效",
        choices=("左对齐", "居中", "靠右"),
        group="notice",
    ),
    ConfigSpec(
        "notice_x_dp",
        INT,
        DEVICE,
        10,
        "提示水平边距",
        "左对齐时是距屏幕左边缘的距离，靠右对齐时是距屏幕右边缘的距离，单位为 dp；"
        "居中时不生效。屏幕固定 1366×768；填到超过屏幕宽度的值会让文字落到屏幕外",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="notice",
    ),
    ConfigSpec(
        "notice_y_dp",
        INT,
        DEVICE,
        6,
        "提示上边距",
        "文字上边缘距屏幕顶部的距离，单位为 dp。屏幕固定 1366×768；填到超过屏幕高度的值会让文字落到屏幕外",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="notice",
    ),
    # ── 提示条件 ──
    *_condition_specs("notice", "notice_condition", "提示"),
    # ── 楼层状态行 ──
    # 内容（各楼层空闲喷头数）由服务端汇总后推送；这里只管"本设备怎么显示它"。
    # 与屏幕提示是两个独立的窗口，位置各调各的，互不挤占。
    ConfigSpec(
        "status_line_enabled",
        BOOL,
        DEVICE,
        True,
        "显示楼层状态行",
        "在屏幕顶部单独显示一行各楼层的空闲喷头数，例如「各楼层空闲 1F 3 · 2F 7 · 3F 0」。"
        "它是与屏幕提示并列的独立窗口；关闭时不创建这一层窗口",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_text_size_sp",
        INT,
        DEVICE,
        28,
        "状态行字号",
        "单位为 sp，跟随系统字体缩放。默认 13sp 是辅助信息的大小——它常驻屏幕上，比操作遮罩和提示小，不抢注意力",
        minimum=10,
        maximum=200,
        unit="sp",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_text_color",
        TEXT,
        DEVICE,
        "#00cc66",
        "状态行文字颜色",
        "十六进制颜色，如 #ffffff；也可写 #AARRGGBB 带透明度。它用于标题与分隔符；"
        "「按状态」配色时各楼层自己的数字另有颜色。格式无法识别时显示白色，并在设备日志留痕",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_available_color",
        TEXT,
        DEVICE,
        "#00cc66",
        "空闲楼层颜色",
        "「按状态」配色下，空闲喷头数大于 0 的楼层文字颜色。十六进制颜色，如 #6bc600；"
        "也可写 #AARRGGBB 带透明度。默认取浴室 App 资源里的 `title_color` #6bc600；"
        "格式无法识别时显示白色，并在设备日志留痕",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_unavailable_color",
        TEXT,
        DEVICE,
        "#999999",
        "不可用楼层颜色",
        "「按状态」配色下，0 个空闲、离线或无数据的楼层文字颜色。默认取浴室 App 资源里的"
        "`zitihui` #999999；与空闲绿保持足够的明度差，不靠色相区分也能看出有没有空位。"
        "格式无法识别时显示白色，并在设备日志留痕",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_background",
        BOOL,
        DEVICE,
        False,
        "显示状态行背景",
        "在文字后面加一层背景色，避免浅色浴室界面上文字看不清；关闭后只留文字",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_background_color",
        TEXT,
        DEVICE,
        "#110066cc",
        "状态行背景颜色",
        "开启背景时使用的颜色，十六进制，可写 #AARRGGBB 带透明度。默认取浴室 App 资源里的"
        "`background_blue` #0066cc 加八成不透明度，让状态行融进浴室界面而不是一块黑底。"
        "格式无法识别时不显示背景，并在设备日志留痕",
        group="status_line",
    ),
    ConfigSpec(
        "status_line_y_dp",
        INT,
        DEVICE,
        0,
        "状态行上边距",
        "状态行上边缘距屏幕顶部的距离，单位为 dp。默认贴着顶部，与屏幕提示的 40dp 错开；"
        "屏幕固定 1366×768，填到超过屏幕高度的值会让文字落到屏幕外",
        minimum=0,
        maximum=2000,
        unit="dp",
        group="status_line",
    ),
    # ── 状态行条件 ──
    # 与屏幕提示共用同一套判定（时间窗 / 本地占用），但值是独立的一组：两个窗口
    # 可以各自显示、各自隐藏，互不牵连。
    *_condition_specs("status_line", "status_line_condition", "状态行"),
    ConfigSpec(
        "automation_trace_enabled",
        BOOL,
        DEVICE,
        False,
        "记录自动化耗时",
        "把分配/结算流程的分段耗时写进 /sdcard/ntu-shower/automation.log，"
        "用 device.exec 可以直接 cat 取回。默认关：每次记录都要开一次文件，长期开着是"
        "无谓的开销，而它的用途本来就是「要量的时候量一轮」",
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
        "默认 1024 条（约 200KB 内存）远超所需",
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
        "10 秒会在浴室 App 完成冷启动之前判定流程卡死。超预算会中止并判失败，调小会误杀"
        "正在缓慢恢复的界面",
        minimum=1000,
        maximum=60000,
        unit="毫秒",
        group="automation",
    ),
    # ── 浴室 App 数据库 ──
    ConfigSpec(
        "bath_db_archive_enabled",
        BOOL,
        DEVICE,
        True,
        "启用数据库归档",
        "浴室 App 的 bath.db 随会话只增不减，涨大后 App 明显卡顿。开启后，本程序每次"
        "启动时检查库大小，攒到阈值即把它改名归档（**永久保留**，归档文件不带自动"
        "清理），浴室 App 下次打开原路径时自动建一个全新的空库。改名对运行中的 App "
        "无害（它持有的文件句柄跟着走，实测无感知）；每晚断电重启，归档后第二天一定"
        "是新库。默认开启",
        group="bath_db",
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
        "renew_wait_students",
        TEXT_MAP,
        SERVER,
        {},  # 可变默认值：defaults() 每次返回新副本，勿在此就地修改
        "预约续期名单",
        "只对这些学号做预约续期，一行一个（格式：学号=密码）。留空表示谁都不做。"
        "**凭证跟着这份名单走**：同一个学号要开两个功能，就在那两份名单里各写一遍——"
        "四份名单各自完整、互不依赖，配一份就读得懂一份",
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
        "auto_settle_students",
        TEXT_MAP,
        SERVER,
        {},
        "自动结算名单",
        "只对这些学号做自动结算，一行一个（格式：学号=密码）。留空表示谁都不做",
        group="auto_settle",
    ),
    ConfigSpec(
        "hold_enabled",
        BOOL,
        SERVER,
        False,
        "启用延长服务时间",
        "浴室 App 每日按平台下发的时刻关机，并在关机前释放全部喷头。开启后，名单内学生"
        "仍在占用的夜晚，服务端按设备上报的计划自动施加 hold（钟面偏置 + 一次布防）,"
        "让服务跨过平台的关机时刻；也可在设备页对单台手动建立今晚 hold。全部时刻按设备"
        "上报的平台计划自适应，本页不定任何时刻",
        group="power",
    ),
    ConfigSpec(
        "hold_students",
        TEXT_MAP,
        SERVER,
        {},
        "延长服务时间名单",
        "只在这些学号还有会话时才自动 hold，一行一个（格式：学号=密码）。留空表示谁都不做。"
        "**凭证跟着这份名单走**：hold 之后要替他们重新分配喷头，那一步要用密码——"
        "四份名单各自完整，配一份就读得懂一份。设备页的「今晚延长」不受此名单限制",
        group="power",
    ),
    # ── 自动校时 ──
    ConfigSpec(
        "time_sync_enabled",
        BOOL,
        SERVER,
        False,
        "启用自动校时",
        "服务端每 30 秒检查一次设备钟偏差，超过阈值即把设备钟一次性写准（设备端随后按"
        "自己上报的计划重布 MCU）。设备没有 NTP，时钟靠 RTC 维持、会跑偏（实测快过整 "
        "1 小时）；依赖开关机计划准确时应开启。三种情形自动让位：hold 期间（偏差是故意"
        "的）、设备上有人占用（校钟的跳变会清掉会话）、还没有偏差记录（无从判）。设备页"
        "的「校正时间」是手动动作，不受此开关影响。默认关闭",
        group="server_clock",
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
