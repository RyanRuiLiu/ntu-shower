"""新库新增的枚举。

既有词汇不在这里重抄：门状态、能力位、标识来源、配置类型都从原模块导入，
保持单一来源。这里只收录现有代码里还没有以枚举形式存在的取值集合。
"""

from enum import StrEnum


class IdentityKind(StrEnum):
    """标识来源。

    与 capability 分开：能力回答"能做什么"，标识类型回答"这个标识从哪来"。
    放在 models 层，让 ORM 模型与持久化层不必反向依赖业务模块。
    """

    OPENID = "openid"
    """微信小程序用户：标识即其 openid。"""

    WEB = "web"
    """网页用户：标识是我方签发的不透明串。"""


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
    identity 留给名单制调用方（X-Identity 时代的历史取值，转换工具会搬进来）。
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


class SnapshotSource(StrEnum):
    """原始帧的来源。"""

    IMPORT = "import"
    STARTUP = "startup"
    DB_WATCH = "db_watch"


class BackgroundOutcome(StrEnum):
    """后台任务的执行结果。

    取值与 scheduler 的日志事件名后缀同一套（`background-task.<它>`）：
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


class CommandStatus(StrEnum):
    """命令结果状态，与 core.outcomes.VALUES 必须完全一致。

    取值定义在 `core/outcomes.py`（它是跨端契约的唯一来源），这里是为了让数据库列
    有可映射的类型。两者的一致性由测试对账，不在两处各自维护。
    """

    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


__all__: list[str] = [
    "ASSIGNABLE_STATES",
    "OCCUPIED_STATES",
    "ActorKind",
    "AssignedSource",
    "BackgroundOutcome",
    "CommandStatus",
    "DeviceLogKind",
    "DoorState",
    "IdentityKind",
    "LinkEvent",
    "ObservedSpan",
    "SegmentSource",
    "SnapshotSource",
]
