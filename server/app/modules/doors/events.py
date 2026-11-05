"""门域可播报事件，供 voice 等下游消费。

- `assigned` / `released` 是**会话生命周期事实**：以会话 id 为身份（一场会话各一次）。
  `assigned` 随新会话建立产生，`startup`/盲区帧只要 `open_time` 可平移也会补发，
  用真实分配时刻而不是观测时刻。
- `shower_started` / `paused` / `resumed` 是**观测到的状态迁移**：以本帧
  `door_state_event` 的行 id 为身份；盲区/全貌帧不编造迁移。

`doors` 不依赖 `voice`；这里只产出门域事实，是否播报由 voice 决定。
"""

from dataclasses import dataclass
from enum import StrEnum


class DoorEventKind(StrEnum):
    """七种播报事件。取值是跨端契约的一部分，中文名由 voice 的事件目录给出。

    `released` 按释放前最后一帧的占用状态拆成三种：未开阀就被释放（预约未到/续期
    场景）、淋浴后释放（正常结算）、暂停中被释放。三者的播报内容语义不同——「本次
    淋浴 X 分钟」只对淋浴后释放成立。
    """

    ASSIGNED = "assigned"
    SHOWER_STARTED = "shower_started"
    PAUSED = "paused"
    RESUMED = "resumed"
    RELEASED_UNUSED = "released_unused"
    RELEASED_AFTER_SHOWER = "released_after_shower"
    RELEASED_WHILE_PAUSED = "released_while_paused"


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
