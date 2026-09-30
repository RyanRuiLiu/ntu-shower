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
    # ── 自动化 ──
    ConfigSpec(
        "overlay_enabled",
        BOOL,
        DEVICE,
        True,
        "显示操作遮罩",
        "自动操作期间是否在屏幕上显示提示层",
    ),
    ConfigSpec(
        "overlay_message",
        TEXT,
        DEVICE,
        "自动操作中…",
        "操作遮罩文字",
        "自动操作期间显示在屏幕提示层上的文字",
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
        200,
        "界面轮询间隔",
        "等待界面元素出现时的轮询周期",
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
        "bridge_idle_timeout_s",
        INT,
        SERVER,
        300,
        "调试通道空闲超时",
        "无数据往来达到该时长即关闭。服务器同时承担其他服务，不留死连接",
        minimum=30,
        maximum=3600,
        unit="秒",
    ),
    ConfigSpec(
        "bridge_max_lifetime_s",
        INT,
        SERVER,
        3600,
        "调试通道最长开启时长",
        "从打开算起的最长存活时长，到点强制关闭。空闲超时只覆盖「没人用」的情况，一直有流量时它不会触发",
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
