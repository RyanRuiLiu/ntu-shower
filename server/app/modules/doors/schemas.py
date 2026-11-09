"""喷头领域的 API 契约：只有 DoorView 一个视图模型。

权限管的是能不能调接口，不是返回什么内容：任何返回喷头数据的接口给的都是
同一套字段，前端只写一套渲染。
"""

from ...contracts import DoorView as DoorView
from ...models.enums import DoorState

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
