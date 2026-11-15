"""水控板直控 API：授权出水、断水、读板信息（`/devices/{device_id}/water/<动词>`）。

## 与会话域的分工

- `session.assign` / `session.settle` 驱动浴室 App 的界面，建会话、写 App 的库；
- 本模块直连水控板（设备端读 `data.xml` 的 waterList 拿板坐标），**不建会话、不写
  浴室 App 数据库、不读写 App 的会话状态**。两者是并列通路，不是主备。

`water.open` 只置授权位（0x01），**不直接出水**；0x02 是按钮位（现场电磁阀），0x04
是出水位，现场按下电磁阀才出现。`water.close` 清 0x01/0x04 断水，不清 0x02。

## 数据源（设备端）

- 控制/门数/板坐标：`data.xml` 的 `waterList` + `BathControlPosno`；
- 板配置展示：板子 `F4 B1`（可能是 24 门/30 分钟，只展示）；
- 板状态：`F4 B3`，只取 `waterList.nozzlenumber` 条，多出的门不返回。

本域的 `nozzle`（喷头位）与 doors 域的 `door`（门位）指同一物理位置：两个词各自
锚定各自的外部来源——`t_doorlist`（浴室 App 的表）与 `waterList.nozzlenumber`
（水控板的表），不互相改写。

服务端只发 `door_no`，不解析 waterList，也不保存板坐标。设备端返回的目标/原包在
`CommandOutcome.data` 里，随 `command_audit` 留档。

## 守卫

`water:write`（直控能绕过会话与计费，只给管理面）；状态读是 `water:read`。
对单台设备均做对象级 device_scope 检查；设备须已信任在线（`device_action`）。
"""

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard
from app.contracts import CommandOutcome
from app.core import actions
from app.db.deps import get_db
from app.modules.devices.dependencies import device_action

from .schemas import WaterDoorRequest

#: 一次授权/断水是板子 TCP 请求-响应；一次状态是 F4 B1 + F4 B3 两次短连接。
#: 设备端每步读 3 秒上限（状态是 B1+B3 两次短连接），服务端留整条动作的余量；
#: 超时只说明板子没回，不是"等设备慢慢做"。
WATER_ACTION_TIMEOUT_S = 8.0

router = APIRouter(prefix="/api/v1/devices", tags=["water"])


@router.post(
    "/{device_id}/water/open",
    dependencies=[Security(caller, scopes=["water:write"]), Depends(device_scope_guard)],
    summary="授权出水",
)
async def open_valve(device_id: str, req: WaterDoorRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """授权出水：置指定喷头的授权位；**不直接出水**，真正出水仍需现场按电磁阀。"""
    return await device_action(
        session,
        device_id,
        actions.WATER_OPEN,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.post(
    "/{device_id}/water/close",
    dependencies=[Security(caller, scopes=["water:write"]), Depends(device_scope_guard)],
    summary="断水",
)
async def close_valve(device_id: str, req: WaterDoorRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """断水：清授权位与出水位；不清按钮位。"""
    return await device_action(
        session,
        device_id,
        actions.WATER_CLOSE,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.get(
    "/{device_id}/water",
    dependencies=[Security(caller, scopes=["water:read"]), Depends(device_scope_guard)],
    summary="读板配置与状态",
)
async def read_status(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """读该设备 waterList 里每块板的配置与状态。

    结果 `data.boards[]`：waterList 的板信息、`F4 B1` 的 `door_num/bath_min`、
    只含 `waterList.nozzlenumber` 条的 `doors[]`。板子多出的门不返回。
    """
    return await device_action(
        session,
        device_id,
        actions.WATER_STATUS,
        {},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )
