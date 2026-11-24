"""电源计划域 API：今晚延长服务（hold）与恢复（resume）。

端点挂在 `/api/v1/devices/{device_id}` 下——hold 是对一台设备电源计划的临时例外。
守卫与管理面一致：`devices:write` + 对象级 device_scope（router 级依赖）。

hold 不要求设备在线：意图持久在影子行上，离线错过触发窗由对账器按
「close 已过」守卫作废；resume 把墙钟写回真实时刻（hold 自己的第二步）后清状态。
尚未施加的意图没有拨钟可写回，恢复只清状态、不发命令（离线亦可取消）；已施加的
写不进（离线、事实缺失）则 hold 保持，以 problem+json 报告失败，等下一轮重试。
"""

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard
from app.contracts import HoldSource
from app.core.errors import problem_responses
from app.db.deps import get_db

from . import service
from .schemas import HoldStatus, ResumeResult

router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Depends(device_scope_guard)],
    tags=["power"],
    responses=problem_responses(401, 403, 404, 409, 422, 502, 504),
)


@router.post(
    "/{device_id}/hold",
    dependencies=[Security(caller, scopes=["devices:write"])],
    summary="今晚延长服务（hold）",
)
def hold_device(device_id: str, session: Session = Depends(get_db)) -> HoldStatus:
    """建立今晚的 hold 意图：对账器在触发窗内施加偏置。

    已在 hold 中时幂等返回现状。触发与恢复全部按设备上报的平台计划换算，
    本端点不定任何时刻。
    """
    return service.request_hold(session, device_id)


@router.get(
    "/{device_id}/hold",
    dependencies=[Security(caller, scopes=["devices:read"])],
    summary="今晚延长服务状态",
)
def hold_status(device_id: str, session: Session = Depends(get_db)) -> HoldStatus:
    """今晚延长服务的只读现状：不在 hold 中时 404。纯读——登记意图走 POST /hold。"""
    return service.hold_status(session, device_id)


@router.post(
    "/{device_id}/resume",
    dependencies=[Security(caller, scopes=["devices:write"])],
    summary="恢复常规电源计划",
)
async def resume_device(device_id: str, session: Session = Depends(get_db)) -> ResumeResult:
    """把墙钟写回真实时刻、清 hold 列，回到 following_schedule。

    成功族返回 `ResumeResult`：applied 写回钟并清状态、pending 零命令取消、本来
    不在延长中三种幂等结果。失败（正在写入设备、布防已写入但拨钟未完成、事实缺失、
    设备离线/超时/失败）一律 problem+json；`hold-applying` 与 `hold-arming-pending`
    带 Retry-After。
    """
    return await service.resume(session, device_id, origin=HoldSource.MANUAL)


__all__ = ["router"]
