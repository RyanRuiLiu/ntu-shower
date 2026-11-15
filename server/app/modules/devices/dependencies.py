"""设备域请求依赖：设备动作的公共下发路径。

`device_action` 是「守卫 + 下发 + 等结果」这一串的唯一实现，`/devices/*` 下的动作端点
（devices 域自己的一批、免费名单域的一批）共用它——差别只在动作名、参数与超时，
各写一遍迟早分叉。守卫本体在 auth.dependencies：config / bridge /
sessions 的写路径也要过同一道闸，放这里会与 devices → config 的依赖方向顶死。
"""

from sqlalchemy.orm import Session

from app.auth.dependencies import require_trusted_online_device
from app.contracts import CommandOutcome
from app.core.protocol import JsonObject

from . import actions

#: 设备动作的默认超时：覆盖 root shell 执行、截图编解码这类耗时动作。
DEVICE_ACTION_TIMEOUT_S = 30.0


async def device_action(
    session: Session, device_id: str, action: str, params: JsonObject, timeout_s: float
) -> CommandOutcome:
    """远程动作的公共路径：设备须已信任在线，命令经统一下发与等待取回结果。"""
    require_trusted_online_device(device_id, session)
    return await actions.send_command_and_wait(session, device_id, action, params, timeout_s=timeout_s)
