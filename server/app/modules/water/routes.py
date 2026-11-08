"""水控板直控 API：授权出水、断水、读板信息（`POST /devices/{device_id}/water/<动词>`）。

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

服务端只发 `door_no`，不解析 waterList，也不保存板坐标。设备端返回的目标/原包在
`CommandOutcome.data` 里，随 `command_audit` 留档。

## 守卫

全部 admin：手动直控能绕过会话与计费，先只给管理员。设备须已信任在线（`device_action`）。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core import actions
from app.db.deps import get_db
from app.modules.access.dependencies import require_admin
from app.modules.devices.dependencies import device_action
from app.modules.sessions.schemas import CommandOutcome

from .schemas import WaterDoorRequest

#: 一次授权/断水是板子 TCP 请求-响应；一次状态是 F4 B1 + F4 B3 两次短连接。
#: 设备端每步读 3 秒上限（状态是 B1+B3 两次短连接），服务端留整条动作的余量；
#: 超时只说明板子没回，不是"等设备慢慢做"。
WATER_ACTION_TIMEOUT_S = 8.0

router = APIRouter(prefix="/api/v1/devices", dependencies=[Depends(require_admin)], tags=["water"])


@router.post("/{device_id}/water/open")
async def open_valve(device_id: str, req: WaterDoorRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """授权出水：置指定喷头的授权位；**不直接出水**，真正出水仍需现场按电磁阀。"""
    return await device_action(
        session,
        device_id,
        actions.WATER_OPEN,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.post("/{device_id}/water/close")
async def close_valve(device_id: str, req: WaterDoorRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """断水：清授权位与出水位；不清按钮位。"""
    return await device_action(
        session,
        device_id,
        actions.WATER_CLOSE,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.post("/{device_id}/water/status")
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
