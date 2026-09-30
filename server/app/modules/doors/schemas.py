"""喷头领域的 API 契约。

`DoorView` 同时承载"门现态"与"所属设备"两项信息：列表接口要联设备表才能告诉
前端"这扇门属于哪台设备、那台设备是否在线"，拆成两个模型只会让前端自己拼。
"""

from enum import Enum

from typing_extensions import TypedDict


class DoorState(str, Enum):
    """喷头的派生状态。

    设备只上报 4 个原始整数（`status_raw`），这 7 个状态由服务端派生
    （规则见 `service.derive_state`）。定义成枚举而非裸字符串，是为了让
    OpenAPI 生成 `enum`：客户端据此生成联合类型，**不必手抄一份状态清单**。

    用 `str, Enum` 而非 `StrEnum`：运行时是 Python 3.10，后者 3.11 才有。
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


#: 占用中的状态（有人）。用于会话维护与自动结算判断。
OCCUPIED_STATES = frozenset({DoorState.WAITING, DoorState.BATHING, DoorState.PAUSED})


class DoorView(TypedDict):
    """一扇喷头的现态（联所属设备的在线状态与名称）。

    字段对应 repository.list_doors_with_device 的 SELECT 列表，改 SQL 时同步改此处。

    `device_online` / `device_name` 可空：查询用的是 LEFT JOIN，若门现态存在而
    设备记录不在（理论上不会——门现态正是设备上报的），二者为 NULL。可空是
    对查询形状的诚实描述，不是预期会发生的情况。

    隐私提示：`card_no` 与 `cert_no` 是占用者的凭证（学号），面向普通用户的
    查看接口不应返回它们。
    """

    device_id: str
    door_no: int
    state: DoorState
    card_no: int
    cert_no: str
    bath_time_s: int
    reported_at: str
    device_online: bool | None
    device_name: str | None


class DoorDetail(TypedDict):
    """一扇喷头的**完整**现态（door_live 的全列）。

    与 `DoorView` 的区别在用途：那个要联设备表、一次返回多台设备的门，只取展示
    必需列；这个供影子文档回答"这台设备现在到底怎么样了"，因此把全列都给出。
    """

    door_no: int
    state: str
    card_no: int
    cert_no: str
    acc_no: str
    open_time: str
    in_time: str
    close_time: str
    bath_time_s: int
    status_raw: int
    reported_at: str
