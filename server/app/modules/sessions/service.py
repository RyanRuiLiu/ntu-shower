"""会话域服务：分配、结算的领域入口（HTTP 与后台任务共用）。

从 routes 下沉的原因：scheduler（自动结算/续期）与 extend_hours（延长服务）
复用同一套「预检 + 下发 + 过载拒绝」，领域入口不该住在 HTTP 层里——否则
scheduler 与他模块的 service 反向 import routes，导入顺序脆弱且有循环。

命令传输（下发、排队、审计、等待）与过载转换（reject_overloaded）都在
devices.actions：那是设备 I/O，不属于任何单个业务功能。
"""

from sqlalchemy.orm import Session

from app.auth.dependencies import require_trusted_online_device
from app.contracts import CommandOutcome
from app.core import actions
from app.core.config import settings
from app.core.problems import (
    DOOR_NOT_FOUND,
    DOOR_NOT_FREE,
    DUPLICATE_SESSION,
    NO_OCCUPIED_DOOR,
    ApiProblemError,
)
from app.modules.devices import actions as device_actions
from app.modules.doors import service as doors_service
from app.modules.sessions.schemas import AssignRequest, SettleRequest


def _require_assignable_door(session: Session, device_id: str, door_no: int) -> None:
    """分配前的喷头现态预检。

    两种失败分开报：喷头从未上报过是 404（编号不存在），状态不可分配是 409。
    判据在 doors 域——那扇门此刻能不能分配是它的事，这里只负责转成领域异常；
    喷头编号不硬编码，存在哪些喷头是设备上报的事实。
    """
    found = doors_service.can_assign(session, device_id, door_no)
    if found is None:
        raise ApiProblemError(DOOR_NOT_FOUND, f"喷头 {door_no} 从未被上报")
    assignable, raw_state = found
    if not assignable:
        raise ApiProblemError(DOOR_NOT_FREE, f"喷头 {door_no} 当前状态：{raw_state}")


def _reject_duplicate_session(session: Session, device_id: str, cert_no: str) -> None:
    """同一学号已有活跃会话则拒绝。"""
    door_no = doors_service.open_session_door(session, device_id, cert_no)
    if door_no is not None:
        raise ApiProblemError(DUPLICATE_SESSION, f"该学号已在喷头 {door_no} 有进行中的会话")


async def perform_assign(session: Session, device_id: str, req: AssignRequest) -> CommandOutcome:
    """分配喷头的领域逻辑，所有调用方共用。"""
    require_trusted_online_device(device_id, session)
    _require_assignable_door(session, device_id, req.door_no)
    _reject_duplicate_session(session, device_id, req.cert_no)
    result = await device_actions.send_command_and_wait(
        session,
        device_id,
        actions.SESSION_ASSIGN,
        {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.assign_timeout_s,
    )
    return device_actions.reject_overloaded(result)


async def perform_settle(session: Session, device_id: str, req: SettleRequest) -> CommandOutcome:
    """结算的领域逻辑。"""
    require_trusted_online_device(device_id, session)
    if not doors_service.is_occupied(session, device_id, req.cert_no):
        raise ApiProblemError(NO_OCCUPIED_DOOR, "该学号没有正在使用的喷头")
    result = await device_actions.send_command_and_wait(
        session,
        device_id,
        actions.SESSION_SETTLE,
        {"cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.settle_timeout_s,
    )
    return device_actions.reject_overloaded(result)
