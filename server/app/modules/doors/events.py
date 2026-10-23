"""门域状态迁移事件：只表达「刚刚发生了什么」，供 voice 等下游消费。

事件的身份是状态迁移本身，不是当前状态：
- `assigned` / `released` 以会话 id 为身份（一场会话各一次）；
- 其余三个以本帧 `door_state_event` 的行 id 为身份。

`doors` 不依赖 `voice`；这里只产出门域事实，是否播报由 voice 决定。
"""

from dataclasses import dataclass
from enum import Enum


class DoorEventKind(str, Enum):
    """五种播报事件。取值是跨端契约的一部分，中文名由 voice 的事件目录给出。"""

    ASSIGNED = "assigned"
    SHOWER_STARTED = "shower_started"
    PAUSED = "paused"
    RESUMED = "resumed"
    RELEASED = "released"


@dataclass(frozen=True)
class DoorEvent:
    """一帧里发生的一次可播报事件。

    `event_key` 是幂等身份：服务端据此在 `voice_delivery` 认领一次事件，
    重复投递或重复派生不会播第二遍。
    """

    kind: DoorEventKind
    device_id: str
    door_no: int
    cert_no: str
    session_id: int
    occurred_at: str
    event_key: str


@dataclass(frozen=True)
class SnapshotResult:
    """一帧的处理结果：处理了多少扇门，以及可播报事件。"""

    processed: int
    events: tuple[DoorEvent, ...]
