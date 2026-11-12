"""设备域请求依赖：跨领域共用的守卫，以及设备动作的下发路径。

独立成模块而非写在 routes.py 里：守卫被 bridge / config / sessions 等多个领域共用，
若定义在路由模块中会造成路由模块之间互相导入（循环依赖），进而迫使调用方使用函数内
导入——那是症状，不是解法。

`device_action` 同理：它是「守卫 + 下发 + 等结果」这一串的唯一实现，`/devices/*` 下的
动作端点（devices 域自己的一批、免费名单域的一批）共用它——差别只在动作名、参数与
超时，各写一遍迟早分叉。
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.problems import DEVICE_NOT_FOUND, DEVICE_OFFLINE, UNTRUSTED_DEVICE, ApiProblemError
from app.core.protocol import JsonObject
from app.db.deps import get_db
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import service
from .schemas import DeviceRecord

#: 设备动作的默认超时：覆盖 root shell 执行、截图编解码这类耗时动作。
DEVICE_ACTION_TIMEOUT_S = 30.0


def require_trusted_online_device(device_id: str, session: Session = Depends(get_db)) -> DeviceRecord:
    """依赖守卫：设备必须存在且已信任、当前在线；否则拒绝进入业务。"""
    device = service.get_device(session, device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    if not device["trusted"]:
        # 设备存在但未信任是 403，不是 404：混淆两者会让客户端以为设备不存在
        raise ApiProblemError(UNTRUSTED_DEVICE, f"设备 {device_id} 尚未被信任")
    if not service.is_online(device_id):
        raise ApiProblemError(DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")
    return device


async def device_action(
    session: Session, device_id: str, action: str, params: JsonObject, timeout_s: float
) -> CommandOutcome:
    """远程动作的公共路径：设备须已信任在线，命令经统一下发与等待取回结果。"""
    require_trusted_online_device(device_id, session)
    return await sessions_service.send_command_and_wait(session, device_id, action, params, timeout_s=timeout_s)
