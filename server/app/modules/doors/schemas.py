"""喷头领域的 API 契约：只有 DoorView 一个视图模型。

权限管的是能不能调接口，不是返回什么内容：任何返回喷头数据的接口给的都是
同一套字段，前端只写一套渲染。
"""

from enum import StrEnum

from typing_extensions import TypedDict


class DoorState(StrEnum):
    """喷头的派生状态，由服务端从 4 个原始整数派生（规则见 service.derive_state）。

    定义成枚举让 OpenAPI 生成 enum，客户端据此生成联合类型，不必手抄状态清单。
    """

    FREE = "free"
    """空闲：可用。"""

    WAITING = "waiting"
    """等待开阀：喷头已分配给这个人，但还没开阀出水。"""

    BATHING = "bathing"
    """淋浴中：正在出水。"""

    PAUSED = "paused"
    """已暂停：出水后暂停，未结束会话。"""

    SELECTED = "selected"
    """选中：被界面选中但未开始使用。"""

    REPAIR = "repair"
    """报修：设备标记为待维修。"""

    DISABLED = "disabled"
    """禁用：设备标记为停用。"""


#: 占用中的状态（有人），用于后台任务与自动结算判断。
OCCUPIED_STATES = frozenset({DoorState.WAITING, DoorState.BATHING, DoorState.PAUSED})

#: 可以被分配的状态。
#:
#: 允许 SELECTED：它表示有人在浴室 App 上选中了这个喷头，但没有人占用，且该状态
#: 会自行消失。拒绝 REPAIR 与 DISABLED：维修中与被停用的喷头不应能被分配。
#: 正着列可分配的状态而不是反着列：将来新增状态默认落到不可分配一侧。
ASSIGNABLE_STATES = frozenset({DoorState.FREE, DoorState.SELECTED})

#: 状态取值 → 界面中文名。**这是机器可读的那一份，与枚举同处一文件。**
#:
#: 与枚举同处是为了让"加一个状态"这件事一眼看得见还要加什么：枚举加了而这里没加，
#: 取值时会 KeyError，而不是在界面上静默显示英文键。管理台的 `views/overview.js`
#: 已经这么做了（缺文案时启动即失败），服务端没有理由更松。
#:
#: `core/terms.py` 的 `CANONICAL` 里也列着这七个名字，但那份是**术语判据**（给人读、
#: 给扫描器查），不是数据源；两份都要与这里一致。
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


class DoorView(TypedDict):
    """一个喷头的现态：door_live 全列 + 上次使用。

    只回答**门域**的问题（这扇门什么状态、上次谁用的）；设备域的事实（在不在线、
    叫什么、能对它做什么）由设备资源回答：GET /devices 与 device.update 推送。
    同一事实只出现在一个接口里，客户端不用调和两份数据源。

    last_used_* 只在喷头没有进行中的会话时才有展示意义；判据是**有没有过段**——"分配后
    直接结算"那种从没出过水的会话说不出任何一段。

    **三个时间字段都在服务端轴上**。为什么对外不能出现设备轴，见 `DECISIONS.md`
    的「两条时间轴」——设备报的原话只留在原始帧里。
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
