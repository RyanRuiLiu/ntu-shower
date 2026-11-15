"""喷头领域的 API 契约与内域视图。

对外（`GET /doors`）是 `contracts.DoorView`。这里的三个内域视图给 mypy strict
一个可校验的形状——列名拼错、漏键当场红；`DoorLiveView` 另承载完整对账列。
"""

from typing_extensions import TypedDict

from app.contracts import DoorState
from app.contracts import DoorView as DoorView

#: 状态取值 → 界面中文名。**这是唯一的数据源。**
#:
#: 与枚举同处是为了让"加一个状态"这件事一眼看得见还要加什么：枚举加了而这里没加，
#: 取值时会 KeyError，而不是在界面上静默显示英文键。管理台的 `views/overview.js`
#: 已经这么做了（缺文案时启动即失败），服务端没有理由更松。
_STATE_LABELS: dict[DoorState, str] = {
    DoorState.FREE: "空闲",
    DoorState.SELECTED: "已选中",
    DoorState.WAITING: "等待开阀",
    DoorState.BATHING: "淋浴中",
    DoorState.PAUSED: "已暂停",
    DoorState.REPAIR: "维修中",
    DoorState.DISABLED: "已禁用",
}


def state_label(state: DoorState) -> str:
    """状态取值的界面中文名。只此一处，客户端与本服务端的其它域都取它。"""
    return _STATE_LABELS[state]


class DoorLiveView(TypedDict):
    """门现态的完整读数：`door_states` 全列，设备原词对账列都在。

    `status_raw` 是设备上报的原始整数——只用于排障对账，判状态一律看 `state`。
    """

    device_id: str
    door_no: int
    state: DoorState
    card_no: int
    cert_no: str
    acc_no: str
    open_time: str
    in_time: str
    close_time: str
    bath_time_s: int
    status_raw: int
    reported_at: str


class DoorSessionView(TypedDict):
    """一场占用的读出形状：人 + 门 + 起止 + 两个口径的时长。"""

    id: int
    device_id: str
    door_no: int
    card_no: int
    cert_no: str
    assigned_at: str
    assigned_source: str
    observed_span: str
    device_flow_s: int
    segments_complete: bool
    ended_at: str
    last_seen_at: str
    total_flow_s: int


class DoorSegmentView(TypedDict):
    """一段连续出水的读出形状。"""

    id: int
    session_id: int
    device_id: str
    door_no: int
    opened_at: str
    closed_at: str
    flow_s: int
    opened_raw: str
    closed_raw: str
    source: str
