"""喷头领域的 API 契约。

**只有一个门视图模型**（[DoorView]）。

早先按接口面分过三份（一个藏起学号与时间给查看类接口用、一个不联设备给影子文档用、
一个联设备且全字段给管理面用）。后果是同一扇门在不同接口里字段不同——前端要写两套
渲染，排障时还得先想"我看的是哪一份"。

**权限管的是能不能调这个接口，不是返回什么内容。** 所以现在统一：任何返回喷头数据的
接口给的都是一套字段。
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
    """禁用：设备标记为停用（原系统的"疫情管控"，对偶数号喷头生效）。"""


#: 占用中的状态（有人）。用于会话维护与自动结算判断。
#
# 与旧系统 `t_doorlist` 里 `openTime <> ''` 的语义等价。
OCCUPIED_STATES = frozenset({DoorState.WAITING, DoorState.BATHING, DoorState.PAUSED})


class DoorView(TypedDict):
    """一扇喷头的现态：`door_live` 全列 + 所属设备 + 上次使用。

    `device_online` / `device_name` 可空：查询用的是 LEFT JOIN，若门现态存在而设备
    记录不在（理论上不会——门现态正是设备上报的），二者为 NULL。可空是对查询形状的
    诚实描述，不是预期会发生的情况。

    `last_used_*` 只在门**没有进行中的会话**时才有展示意义：占用中的门牌显示的是这
    一次的分配与开阀时间，再挂一行"上次"是噪声。判据是**开过阀**（`opened_at` 非空）
    而不是洗了多久——分配后直接结算那种从没出过水的不算，而开了阀立刻关的要算。
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
    device_online: bool | None
    device_name: str | None

    last_used_at: str
    """上次开阀时刻（RFC 3339）。从未被真正使用过时为空串。"""

    last_used_s: int
    """那次会话的出水秒数；从未被真正使用过时为 0。"""

    last_used_cert: str
    """那次是谁用的（学号）。从未被真正使用过时为空串。"""
