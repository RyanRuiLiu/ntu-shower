"""共享视图契约：持久化层与业务模块共同消费的数据形状。

这些 TypedDict 只描述"一行记录有哪些字段"或"一次交互的结果"，不含业务规则。
它们不属于任何单个业务模块：生产方按这个形状返回，消费方按这个形状消费。放在
共享层，两边的导入方向都指向这里，避免模块之间互相导入对方的 API 契约。
"""

from __future__ import annotations

from enum import StrEnum

from typing_extensions import TypedDict

from .core.protocol import JsonObject


class DoorState(StrEnum):
    """喷头的派生状态，由服务端从 4 个原始整数派生（规则见 doors.service.derive_state）。"""

    FREE = "free"
    WAITING = "waiting"
    BATHING = "bathing"
    PAUSED = "paused"
    SELECTED = "selected"
    REPAIR = "repair"
    DISABLED = "disabled"


#: 占用中的状态（有人），用于后台任务与自动结算判断。
OCCUPIED_STATES = frozenset({DoorState.WAITING, DoorState.BATHING, DoorState.PAUSED})

#: 可以被分配的状态。允许 SELECTED：选中但无人占用，且会自行消失。
ASSIGNABLE_STATES = frozenset({DoorState.FREE, DoorState.SELECTED})


class ActorKind(StrEnum):
    """调用方类型，与 api_calls.actor_kind 对应。

    token 与 session 是第一批认证标准化引入的两类（Bearer 凭据与会话 Cookie）；
    identity 留给名单制调用方（已删除的自定身份头时代的历史取值，转换工具会搬进来）。
    """

    DEVICE = "device"
    ANONYMOUS = "anonymous"
    IDENTITY = "identity"
    TOKEN = "token"
    SESSION = "session"


class LinkEvent(StrEnum):
    """设备链路事件。"""

    ONLINE = "online"
    OFFLINE = "offline"


class DeviceLogKind(StrEnum):
    """设备上送日志的种类。"""

    DEVICE = "device"
    CRASH = "crash"
    OTA = "ota"


class AssignedSource(StrEnum):
    """会话起点的来源：观测到的，还是推算出来的。"""

    OBSERVED = "observed"
    INFERRED = "inferred"


class ObservedSpan(StrEnum):
    """会话观测的连续性。"""

    CONTINUOUS = "continuous"
    GAP = "gap"


class SegmentSource(StrEnum):
    """段长的来源。"""

    MEASURED = "measured"
    OBSERVED = "observed"
    CAPPED = "capped"
    DERIVED = "derived"


class JobOutcome(StrEnum):
    """后台任务的执行结果。

    取值与 scheduler 的日志事件名后缀同一套（`job.<它>`）：
    日志与审计表两边不分叉。
    """

    SETTLED = "settled"
    ASSIGNED = "assigned"
    SETTLE_FAILED = "settle-failed"
    ASSIGN_WAITING = "assign-waiting"
    ASSIGN_FAILED = "assign-failed"
    ASSIGN_EXPIRED = "assign-expired"
    SKIPPED = "skipped"
    FAILED = "failed"
    REJECTED = "rejected"
    NO_PASSWORD = "no-password"
    NOT_APPLICABLE = "not-applicable"


class ApiCallKind(StrEnum):
    """api_call 的两类来源：HTTP 请求与设备网关生命周期。"""

    HTTP = "http"
    GATEWAY = "gateway"


class CommandStatus(StrEnum):
    """命令结果状态，与 core.outcomes.VALUES 必须完全一致。

    取值定义在 `core/outcomes.py`（它是跨端契约的唯一来源），这里是为了让数据库列
    有可映射的类型。两者的一致性由测试对账，不在两处各自维护。
    """

    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class CommandOutcome(TypedDict):
    """设备命令的终态结果。

    对齐 protocol/schema/session.command_result.json。`data` 按命令而异
    （拍照回 image_base64、远程执行回 output），因此保持开放对象：前端按
    `action` 分支取字段，而非为每种命令各造一个响应类型。
    """

    status: str
    reason_code: str
    terminal_message: str
    data: JsonObject | None


class CommandAuditRecord(TypedDict):
    """一行命令审计：下发、参数与设备回报（与 /audit/calls 以 trace_id 关联）。

    `finished_at`/`status`/`reason_code`/`terminal_message`/`data` 为空（None）
    表示命令尚未收尾——它还在等设备回报或已等待超时。
    """

    command_id: str
    device_id: str | None
    action: str
    params: str | None
    created_at: str
    finished_at: str | None
    status: str | None
    reason_code: str | None
    terminal_message: str | None
    trace_id: str | None
    data: str | None


class JobRun(TypedDict):
    """一行后台任务记录：配置好的任务自动替谁做了什么、为什么。

    `reason` 是没动手时的中文原因（"不在名单里"这类）；`threshold_s` 是当时生效的
    阈值——存进这一行，是为了事后不必去找当时的配置就能看懂"为什么是这一刻"。
    `occurred_at` 是服务端轴：与 `command_audit.created_at` 对照即知这次动作发了哪条命令。
    """

    id: int
    occurred_at: str
    device_id: str | None
    door_no: int
    session_id: int | None
    cert_no: str
    job: str
    action: str | None
    outcome: str
    reason: str | None
    threshold_s: int | None


class DeviceSummary(TypedDict):
    """设备的精简视图：可辨识、在场状态与停用位，用户面与实时推送共用。

    版本、来源 IP 等运维诊断字段不在此列，只出现在完整档案（DeviceRecord）与
    管理面接口里。device.update 的载荷即本形状加 `at` 一项（见 devices.service）。
    """

    device_id: str
    name: str
    online: bool
    retired: bool
    """已停用：非真即正常在用。停用设备拒绝握手与自注册，历史数据全部保留。"""


class DeviceRecord(DeviceSummary):
    """一台设备的档案（= DeviceSummary + 运维诊断字段）。

    trusted 的 0/1 到布尔的转换在 repository 完成。online 不在档案表里，由
    devices.service 从内存连接表填入：只有经过 service 的记录才带正确的值。
    """

    trusted: bool
    app_version: str
    version_code: int
    os_version: str
    capabilities: list[str]
    last_seen_at: str
    last_ip: str
    booted_at: str
    """开机时刻（RFC 3339）：由设备上报的已运行秒数与服务端收到它的时刻反推。

    存时刻而不存秒数：秒数一离开采样瞬间就开始过期，开机时刻是不变的事实；
    前端用"现在减它"得到已运行时长。
    """

    clock_skew_s: float
    """设备钟与服务端的偏差（秒）。由设备回 pong 时带上的墙上时刻算出（见 core/device_clock）。

    它用来把帧里的 `open_time` / `in_time` 平移到服务端时间轴——那两个字段是"这次
    会话从什么时候开始"的唯一线索。**绝对值不可信不代表它没用**：一次会话压在 20 分钟
    以内，而 20 分钟里的漂移只有 0.03 秒，窗口内偏差是常量。
    """

    sort_order: int
    """展示顺序：设备下拉、管理台设备表、统计接口全部按它排。管理员在设备页调整，
    新设备排到末尾；同序回落到名字的自然排序（见 repository.list_all）。"""


class DoorView(TypedDict):
    """一个喷头的现态：door_live 全列 + 上次使用。

    只回答**门域**的问题（这扇门什么状态、上次谁用的）；设备域的事实（在不在线、
    叫什么、能对它做什么）由设备资源回答：GET /devices 与 device.update 推送。
    同一事实只出现在一个接口里，客户端不用调和两份数据源。

    last_used_* 只在喷头没有进行中的会话时才有展示意义；判据是**有没有过段**——"分配后
    直接结算"那种从没出过水的会话说不出任何一段。

    **三个时间字段都在服务端轴上**。为什么对外不能出现设备轴，见
    docs/architecture.md 的「两条时间轴」——设备报的原话只留在原始帧里。
    """

    device_id: str
    door_no: int
    state: DoorState
    card_no: int
    cert_no: str
    acc_no: str

    open_time: str
    """分配时刻（服务端轴）。空闲时为空串。"""

    in_time: str
    """当前这一段的开阀时刻（服务端轴）。

    **暂停后仍有值**：设备在关阀那一刻就把 `inTime` 清空了，而这一段的开阀时刻由服务端
    从段表回答（见 `doors/service` 的 `_apply_snapshot`）。它因此不能用来说"此刻在不在
    出水"，那是 `state` 的事。等待开阀时为空——那时本会话还没有过任何一段。
    """

    close_time: str
    """当前这一段的关阀时刻（服务端轴）。只有暂停时有值。"""

    bath_time_s: int
    """设备累计的出水秒数。它是**时长不是时刻**，不在任何一条时间轴上。"""

    status_raw: int
    reported_at: str

    last_used_at: str
    """上次开阀时刻（RFC 3339）。从未被真正使用过时为空串。"""

    last_used_s: int
    """那次会话的出水秒数；从未被真正使用过时为 0。"""

    last_used_cert: str
    """那次是谁用的（学号）。从未被真正使用过时为空串。"""


class ReleaseRecord(TypedDict):
    """一条已注册的发布。"""

    version: str
    version_code: int
    sha256: str
    mandatory: bool
    force: bool
    target: str
    created_at: str


class Caller(TypedDict):
    """一次请求的调用方：守卫判定的唯一输入形状。

    会话通道（面板操作者）与密钥通道（外部调用方）各解析一次，产出同一个形状，
    守卫逻辑不分叉。scopes 与 device_scope 会话通道恒 `["*"]`（面板即操作台），
    密钥通道取该枚密钥上的值——权限附在密钥上，不附在身份上。
    """

    username: str
    name: str
    scopes: list[str]
    device_scope: list[str]
    key_name: str | None
    """本请求用的密钥名；会话通道为 None。"""


class UserRecord(TypedDict):
    """一行用户记录：名单、登录响应与密钥签发处共用同一形状。"""

    id: int
    username: str
    name: str
    disabled: bool
    has_password: bool
    last_login_at: str
    created_at: str


class KeyRecord(TypedDict):
    """一枚密钥的记录：签发、列表、解析三处共用同一形状。"""

    id: int
    user_id: int
    name: str
    scopes: list[str]
    device_scope: list[str]
    token_hint: str
    """摘要前 12 位：与审计 actor 字段同值，密钥↔审计行按它对账。"""
    last_used_at: str
    created_at: str
    revoked_at: str


__all__: list[str] = [
    "ASSIGNABLE_STATES",
    "OCCUPIED_STATES",
    "ActorKind",
    "ApiCallKind",
    "AssignedSource",
    "Caller",
    "CommandAuditRecord",
    "CommandOutcome",
    "CommandStatus",
    "DeviceLogKind",
    "DeviceRecord",
    "DeviceSummary",
    "DoorState",
    "DoorView",
    "JobOutcome",
    "JobRun",
    "KeyRecord",
    "LinkEvent",
    "ObservedSpan",
    "ReleaseRecord",
    "SegmentSource",
    "UserRecord",
]
