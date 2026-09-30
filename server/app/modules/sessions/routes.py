"""用户 API（v1）：分配 / 结算 / 拍照（匿名，鉴权对齐旧系统模型）。

cert_no/password 是浴室系统的业务凭证：随操作参数直传设备，
真正的凭证验证发生在浴室服务器端（密码错会在终端页被拒）。
本服务不做用户登录；未来的调用方身份体系（微信小程序 / 独立 APP）
按 docs/DECISIONS.md 的 provider 方案另行引入。
设备执行结果以结构化字段返回，terminal_message 为浴室 App 的人读文本。
分配/结算下发前依据 door_live 现态做预检，避免对占用门误触发结算流。
"""

from fastapi import APIRouter, Depends

from app.core import actions, outcomes
from app.core.config import settings
from app.core.problems import (
    DOOR_NOT_FOUND,
    DOOR_NOT_FREE,
    DUPLICATE_SESSION,
    NO_OCCUPIED_DOOR,
    SERVER_OVERLOADED,
    ApiProblemError,
)
from app.core.protocol import JsonObject
from app.core.security import require_ops_token
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import require_capability
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.doors import repository as doors_repository
from app.modules.doors.schemas import ASSIGNABLE_STATES, DoorState
from app.modules.sessions import repository
from app.modules.sessions.schemas import AssignRequest, CommandOutcome, PhotoRequest, SettleRequest

from . import service

router = APIRouter(prefix="/api/v1", tags=["sessions"])

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["sessions-ops"])


def _require_assignable_door(device_id: str, door_no: int) -> None:
    """分配前的门现态预检。

    两种失败分开报：门从没被上报过是 **404**（这个门号不存在），状态不对是 **409**
    （门在，但现在不能分配）。合成一句"不是空闲"会让"喷头 9 不存在"和"喷头 3 里有人"
    看起来是同一回事，而处理方式不同。

    门号范围不在这里硬编码——存在哪些门是设备上报的事实，不是服务端的常量。

    可分配的状态见 [ASSIGNABLE_STATES]：那里是唯一的判定处，本函数只负责把它接上
    HTTP 错误。
    """
    row = doors_repository.get_door_live(device_id, door_no)
    if row is None:
        raise ApiProblemError(DOOR_NOT_FOUND, f"门 {door_no} 从未被上报")
    raw_state = row["state"]
    try:
        state: DoorState | None = DoorState(raw_state)
    except ValueError:
        # 设备上报了本端不认识的状态：不分配，也不去猜它是什么意思
        state = None
    if state not in ASSIGNABLE_STATES:
        raise ApiProblemError(DOOR_NOT_FREE, f"门 {door_no} 当前状态：{raw_state}")


def _reject_duplicate_session(device_id: str, cert_no: str) -> None:
    """同一学号已有活跃会话则拒绝（对齐旧系统 duplicate_session 语义）。"""
    active = doors_repository.get_active_by_cert(device_id, cert_no)
    if active is not None:
        raise ApiProblemError(
            DUPLICATE_SESSION,
            f"学号 {cert_no} 已在门 {active['door_no']} 有进行中的会话",
        )


def _reject_overload(result: CommandOutcome) -> CommandOutcome:
    """通道排队满时把拒绝转成 409。

    注意这**不是**"设备忙"：同通道的先后命令会排队执行，只有队满才拒（见
    `service.CHANNEL_QUEUE_MAX`）。两者语义不同——前者说明设备正忙、等一会就好，
    后者说明积压已多、该换个时间再来。
    """
    if result.get("status") == outcomes.REJECTED.value:
        raise ApiProblemError(SERVER_OVERLOADED, f"原因码：{result.get('reason_code')}")
    return result


async def perform_assign(req: AssignRequest) -> CommandOutcome:
    """分配喷头的领域逻辑。

    用户面与管理面共用——两边的区别只在**怎么授权**，不在做什么。把逻辑写在路由
    函数里会逼出第二份拷贝，那种拷贝迟早会分叉。
    """
    require_trusted_online_device(req.device_id)
    _require_assignable_door(req.device_id, req.door_no)
    _reject_duplicate_session(req.device_id, req.cert_no)
    result = await service.send_command_and_wait(
        req.device_id,
        actions.SESSION_ASSIGN,
        {"door_no": req.door_no, "cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.assign_timeout_s,
    )
    return _reject_overload(result)


async def perform_settle(req: SettleRequest) -> CommandOutcome:
    """结算的领域逻辑。"""
    require_trusted_online_device(req.device_id)
    occupied = doors_repository.get_occupied_by_cert(req.device_id, req.cert_no)
    if occupied is None:
        raise ApiProblemError(NO_OCCUPIED_DOOR, f"学号 {req.cert_no} 没有正在使用的喷头")
    result = await service.send_command_and_wait(
        req.device_id,
        actions.SESSION_SETTLE,
        {"cert_no": req.cert_no, "password": req.password},
        timeout_s=settings.settle_timeout_s,
    )
    return _reject_overload(result)


async def perform_photo(req: PhotoRequest) -> CommandOutcome:
    """拍照的领域逻辑。"""
    require_trusted_online_device(req.device_id)
    result = await service.send_command_and_wait(
        req.device_id,
        actions.CAMERA_CAPTURE,
        {},
        timeout_s=settings.photo_timeout_s,
    )
    return _reject_overload(result)


@router.post("/sessions/assign", dependencies=[Depends(require_capability(Capability.OPS))])
async def assign(req: AssignRequest) -> CommandOutcome:
    """用户面：小程序调用，凭 ops 能力位。"""
    return await perform_assign(req)


@router.post("/sessions/settle", dependencies=[Depends(require_capability(Capability.OPS))])
async def settle(req: SettleRequest) -> CommandOutcome:
    """用户面：小程序调用，凭 ops 能力位。"""
    return await perform_settle(req)


@router.post("/sessions/photo", dependencies=[Depends(require_capability(Capability.PHOTO))])
async def take_photo(req: PhotoRequest) -> CommandOutcome:
    """用户面：小程序调用，凭 photo 能力位。"""
    return await perform_photo(req)


# ── 管理面 ──
#
# 管理页是运维在代操作，**不代表某个终端用户**，所以它凭 `X-Ops-Token` 授权，
# 而不是去借一个用户身份来通过能力位检查。给管理页套用户身份还有一层别扭：
# 那个身份的每一次使用都会被算成"该用户的操作"，审计里就分不清是谁干的。


@ops_router.post("/sessions/assign")
async def ops_assign(req: AssignRequest) -> CommandOutcome:
    """管理面：分配喷头。"""
    return await perform_assign(req)


@ops_router.post("/sessions/settle")
async def ops_settle(req: SettleRequest) -> CommandOutcome:
    """管理面：结算。"""
    return await perform_settle(req)


@ops_router.post("/sessions/photo")
async def ops_take_photo(req: PhotoRequest) -> CommandOutcome:
    """管理面：拍照。"""
    return await perform_photo(req)


@ops_router.get("/commands")
def list_commands(device_id: str | None = None, limit: int = 50) -> list[JsonObject]:
    """命令审计查询：调试与问题追踪。"""
    return repository.list_commands(device_id=device_id, limit=limit)
