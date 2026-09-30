"""喷头领域的 API 契约。

**只有一个门视图模型**（[DoorView]）。

早先按接口面分过三份（一个藏起学号与时间给查看类接口用、一个不联设备给影子文档用、
一个联设备且全字段给管理面用）。后果是同一个喷头在不同接口里字段不同——前端要写两套
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

#: 可以被分配的状态。
#
# **允许 `SELECTED`**：它表示"有人在浴室 App 上选中了这个喷头"，但 `cardno` 仍是 0——
# 没有任何人占用，而这个状态会自行消失（真机实测两次，门自己回到了 free）。旧系统
# 正是这么放行的：它只拒 [OCCUPIED_STATES] 那三种。若在这里要求必须 `FREE`，学生
# 点了选中的那几秒内远程就分配不了，而那不是"有人"。
#
# **拒绝 `REPAIR` 与 `DISABLED`**：旧系统把它们也放行了，那是它的疏漏——维修中与
# 被停用的喷头不该能被分配。这一处与旧系统的差异是有意保留的。
#
# 正着列（可分配的）而不是反着列（`!= FREE`）：将来新增状态时默认落到"不可分配"
# 那一侧，而不是悄悄变成可分配。
ASSIGNABLE_STATES = frozenset({DoorState.FREE, DoorState.SELECTED})


class DoorView(TypedDict):
    """一个喷头的现态：`door_live` 全列 + 所属设备 + 上次使用。

    `device_online` / `device_name` 可空：查询用的是 LEFT JOIN，若喷头现态存在而设备
    记录不在（理论上不会——喷头现态正是设备上报的），二者为 NULL。可空是对查询形状的
    诚实描述，不是预期会发生的情况。

    `last_used_*` 只在喷头**没有进行中的会话**时才有展示意义：占用中的喷头卡片显示的是这
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
