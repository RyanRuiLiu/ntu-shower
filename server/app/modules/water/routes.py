"""水控板直控 API：开阀、关阀、读板信息（`POST /devices/{device_id}/water/<动词>`）。

## 与会话域的分工

- `session.assign` / `session.settle` 驱动浴室 App 的界面，建会话、写 App 的库；
- 本模块直连水控板（设备端读 `data.xml` 的 waterList 拿板坐标），**不建会话、不写
  浴室 App 数据库、不与 App 状态同步**。两者是并列通路，不是主备。

`water.open` 只登记占用（板子置 0x01），**不直接出水**；现场按下电磁阀才会置
0x04 出水。`water.close` 会清 0x04/0x02，真正断水；有卡的喷头可能保留 0x01。

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

from app.core import actions
from app.modules.access.dependencies import require_admin
from app.modules.devices.dependencies import device_action
from app.modules.sessions.schemas import CommandOutcome

from .schemas import WaterDoorRequest

#: 一次开/关是板子 TCP 请求-响应；一次状态是 F4 B1 + F4 B3 两次短连接。
#: 设备端每步读 3 秒上限（状态是 B1+B3 两次短连接），服务端留整条动作的余量；
#: 超时只说明板子没回，不是"等设备慢慢做"。
WATER_ACTION_TIMEOUT_S = 8.0

router = APIRouter(prefix="/api/v1/devices", dependencies=[Depends(require_admin)], tags=["water"])


@router.post("/{device_id}/water/open")
async def open_valve(device_id: str, req: WaterDoorRequest) -> CommandOutcome:
    """登记指定喷头的占用；**不直接出水**，真正出水仍需现场按电磁阀。"""
    return await device_action(
        device_id,
        actions.WATER_OPEN,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.post("/{device_id}/water/close")
async def close_valve(device_id: str, req: WaterDoorRequest) -> CommandOutcome:
    """关指定喷头的电磁阀。"""
    return await device_action(
        device_id,
        actions.WATER_CLOSE,
        {"door_no": req.door_no},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )


@router.post("/{device_id}/water/status")
async def read_status(device_id: str) -> CommandOutcome:
    """读该设备 waterList 里每块板的配置与状态。

    结果 `data.boards[]`：waterList 的板信息、`F4 B1` 的 `door_num/bath_min`、
    只含 `waterList.nozzlenumber` 条的 `doors[]`。板子多出的门不返回。
    """
    return await device_action(
        device_id,
        actions.WATER_STATUS,
        {},
        timeout_s=WATER_ACTION_TIMEOUT_S,
    )
