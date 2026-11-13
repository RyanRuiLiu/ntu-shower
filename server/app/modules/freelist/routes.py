"""免费名单域：读设备上那份名单、整份写回（admin）。

## 端点

`GET` / `PUT /api/v1/devices/{device_id}/freelist`：同一份资源的读与写，写是整份替换
（PUT 的语义）。

端点挂在 `/devices/*` 下，是因为它就是**那台设备上的一份数据**：服务端不存副本，
换台设备就是另一份名单。

## 一次往返换一份行

设备端只搬文件原文（见 `core/actions.py` 里 `freelist.read` 的注释），拆行与拼行只在
本域发生（[service.parse] / [service.render]）。读回来直接给人编辑、交回去拼成原文再
下发——中间没有任何一处做第二次转换。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.contracts import CommandOutcome
from app.core import actions, outcomes
from app.db.deps import get_db
from app.modules.access.dependencies import require_admin
from app.modules.devices.dependencies import DEVICE_ACTION_TIMEOUT_S, device_action

from .schemas import FreelistRead, FreelistRequest
from .service import parse, render

router = APIRouter(prefix="/api/v1/devices", dependencies=[Depends(require_admin)], tags=["freelist"])


@router.get("/{device_id}/freelist")
async def read_freelist(device_id: str, session: Session = Depends(get_db)) -> FreelistRead:
    """读设备上那份免费名单。

    读不到（文件不在、读不出）时如实回报 status 与原因码，students 留空——**不用 HTTP
    错误代替它**：那会把"设备在线但那份文件没了"说成请求本身有问题，而这两件事要查的
    地方完全不同。
    """
    outcome = await device_action(session, device_id, actions.FREELIST_READ, {}, DEVICE_ACTION_TIMEOUT_S)
    # 读失败时 data 为 None，行留空；**不把它当"空名单"**——那两件事要分开看（见 FreelistRead）
    data = outcome["data"]
    students = parse(str(data.get("content", ""))) if outcome["status"] == outcomes.OK.value and data else []
    return {
        "status": outcome["status"],
        "reason_code": outcome["reason_code"],
        "terminal_message": outcome["terminal_message"],
        "students": students,
    }


@router.put("/{device_id}/freelist")
async def write_freelist(device_id: str, req: FreelistRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """整份写回免费名单。

    名单在浴室 App **下次启动**时生效（它启动时读一次），所以这里不跟一步重启：重启会
    掐断进行中的会话，而名单改动不急在那几十秒。
    """
    return await device_action(
        session, device_id, actions.FREELIST_WRITE, {"content": render(req.students)}, DEVICE_ACTION_TIMEOUT_S
    )
