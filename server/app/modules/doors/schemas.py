"""喷头领域的 API 契约：只有 DoorView 一个视图模型。

权限管的是能不能调接口，不是返回什么内容：任何返回喷头数据的接口给的都是
同一套字段，前端只写一套渲染。
"""

from enum import Enum

from typing_extensions import TypedDict


class DoorState(str, Enum):
    """喷头的派生状态，由服务端从 4 个原始整数派生（规则见 service.derive_state）。

    定义成枚举让 OpenAPI 生成 enum，客户端据此生成联合类型，不必手抄状态清单。
    """

    FREE = "free"
    """空闲：可用。"""

    WAITING = "waiting"
    """已入门：已刷卡进入但尚未开始出水。"""

    BATHING = "bathing"
    """使用中：正在出水。"""

    PAUSED = "paused"
    """已暂停：出水后暂停，未结束会话。"""

    SELECTED = "selected"
    """选中：被界面选中但未开始使用。"""

    REPAIR = "repair"
    """报修：设备标记为待维修。"""

    DISABLED = "disabled"
    """禁用：设备标记为停用。"""


#: 占用中的状态（有人），用于会话维护与自动结算判断。
OCCUPIED_STATES = frozenset({DoorState.WAITING, DoorState.BATHING, DoorState.PAUSED})

#: 可以被分配的状态。
#:
#: 允许 SELECTED：它表示有人在浴室 App 上选中了这个喷头，但没有人占用，且该状态
#: 会自行消失。拒绝 REPAIR 与 DISABLED：维修中与被停用的喷头不应能被分配。
#: 正着列可分配的状态而不是反着列：将来新增状态默认落到不可分配一侧。
ASSIGNABLE_STATES = frozenset({DoorState.FREE, DoorState.SELECTED})


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
    """当前这一段的开阀时刻（服务端轴）。只有正在出水时有值。"""

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
