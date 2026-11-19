"""电源计划域 API：今晚延长（hold）与恢复（resume）。

端点挂在 `/api/v1/devices/{device_id}` 下——hold 是对一台设备电源计划的临时例外。
守卫与管理面一致：`devices:write` + 对象级 device_scope（router 级依赖）。

hold 不要求设备在线：意图持久在影子行上，离线错过触发窗由对账器按
「close 已过」守卫作废；resume 要发命令（写回真实钟面），离线 409。
"""

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard
from app.core.errors import problem_responses
from app.db.deps import get_db

from . import service
from .schemas import HoldStatus, PowerActionResult

router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Depends(device_scope_guard)],
    tags=["power"],
    responses=problem_responses(401, 403, 404, 409, 422),
)


@router.post(
    "/{device_id}/hold",
    dependencies=[Security(caller, scopes=["devices:write"])],
    summary="今晚延长（hold）",
)
def hold_device(device_id: str, session: Session = Depends(get_db)) -> HoldStatus:
    """建立今晚的 hold 意图：对账器在下一轮（≤30 秒）施加偏置。

    已在 hold 中时幂等返回现状。触发与恢复全部按设备上报的平台计划换算，
    本端点不定任何时刻。
    """
    return service.request_hold(session, device_id)


@router.post(
    "/{device_id}/resume",
    dependencies=[Security(caller, scopes=["devices:write"])],
    summary="恢复常规电源计划",
    responses=problem_responses(409),
)
async def resume_device(device_id: str, session: Session = Depends(get_db)) -> PowerActionResult:
    """写回真实钟面并按设备上报的计划恢复常规布防。未在 hold 中时幂等（resumed=false）。"""
    result = await service.resume(session, device_id)
    return PowerActionResult(
        device_id=str(result["device_id"]),
        resumed=bool(result["resumed"]),
    )


__all__ = ["router"]
