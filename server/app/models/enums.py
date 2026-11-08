"""新库新增的枚举。

既有词汇不在这里重抄：门状态、能力位、标识来源、配置类型都从原模块导入，
保持单一来源。这里只收录现有代码里还没有以枚举形式存在的取值集合。
"""

from enum import StrEnum


class ActorKind(StrEnum):
    """调用方类型，与 api_call.actor_kind 对应。"""

    DEVICE = "device"
    ANONYMOUS = "anonymous"
    IDENTITY = "identity"


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
    """后台任务的执行结果。"""

    SETTLED = "settled"
    ASSIGNED = "assigned"
    SETTLE_FAILED = "settle-failed"
    ASSIGN_WAITING = "assign-waiting"
    ASSIGN_FAILED = "assign-failed"
    SKIPPED = "skipped"


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
    "ActorKind",
    "AssignedSource",
    "BackgroundOutcome",
    "CommandStatus",
    "DeviceLogKind",
    "LinkEvent",
    "ObservedSpan",
    "SegmentSource",
    "SnapshotSource",
]
