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
    """一个喷头的现态：door_live 全列 + 所属设备 + 上次使用。

    device_name 可空：LEFT JOIN 无匹配时为 NULL，保持 NULL 而不是折成 False，
    "设备记录不存在"与"设备离线"不是一回事。device_online 恒有值：来自内存
    连接表，由 doors.service.list_doors 填入。

    last_used_* 只在喷头没有进行中的会话时才有展示意义；判据是开过阀
    （opened_at 非空），分配后直接结算那种从没出过水的不算。
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
    device_online: bool
    device_name: str | None

    last_used_at: str
    """上次开阀时刻（RFC 3339）。从未被真正使用过时为空串。"""

    last_used_s: int
    """那次会话的出水秒数；从未被真正使用过时为 0。"""

    last_used_cert: str
    """那次是谁用的（学号）。从未被真正使用过时为空串。"""
