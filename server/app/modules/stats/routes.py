"""统计域的 API：一台设备一次装载、某一天的状态序列、个人统计。

## 端点是同步的

统计是纯 CPU 的聚合（实测一台设备近 30 天约 46 ms，热力矩阵占其中七成）。FastAPI
把同步端点丢进线程池，事件循环因此不会被它挡住；写成异步反而会让这段 CPU 密集的
代码跑在事件循环上，把设备帧的处理一起拖慢。

本域不像 `tasks` 那样必须是异步——那里要 `reload()` 定时器，而那个函数取
`asyncio.get_running_loop()`。这里没有这类调用。

## 三个端点为什么不是一个

它们被三个不同的时机调用，参数与数据源都不同：

- 主视图按窗口（`days`）取一台设备的聚合，是页面主体；
- 回放按**日期**取一天的状态序列，数据源是 `door_state_event` 而不是会话表；
- 个人统计**跨设备**，与主视图的单设备语义相反。

合成一个端点就要让每个请求都带上一半用不着的参数，并且把按需的回放数据也算一遍。
"""

from fastapi import APIRouter, Depends, Query

from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import require_capability, require_identity
from app.modules.access.schemas import IdentityRecord
from app.modules.devices import repository as devices

from . import service
from .schemas import MineView, ReplayView, StatsView

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

#: 时区偏移的参数说明。方向容易搞反，所以写在两处端点共用的常量里而不是各写一遍。
_TZ_QUERY = Query(
    480,
    ge=-720,
    le=840,
    description="本地时间减 UTC 的分钟数。UTC+8 传 480；注意 JavaScript 的 getTimezoneOffset() 给的是反的",
)


@router.get("/devices/{device_id}")
def device_stats(
    device_id: str,
    caller: IdentityRecord = Depends(require_identity),
    days: int = Query(30, ge=1, le=365, description="统计窗口的天数"),
    tz_offset_minutes: int = _TZ_QUERY,
) -> StatsView:
    """一台设备一次装载：今日、热力图、两个分布、喷头热度、七个榜、各楼层对比。

    切设备与切窗口要重拉；榜的页签与方向、分布、回放的拖动都在客户端本地切，不发请求。
    """
    require_capability(caller, Capability.STATS)
    if devices.get(device_id) is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return service.device_stats(device_id, days, tz_offset_minutes)


@router.get("/devices/{device_id}/replay")
def device_replay(
    device_id: str,
    caller: IdentityRecord = Depends(require_identity),
    day: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="当地日期 YYYY-MM-DD"),
    tz_offset_minutes: int = _TZ_QUERY,
) -> ReplayView:
    """那一天的完整状态变化序列。

    一次拉一天，客户端按滑块位置重放；拖动的每一步都不发请求。按"给定时刻查现态"的
    写法要扫到那一刻为止的全部历史事件，而滑块每移动一格就触发一次，那样会卡。
    """
    require_capability(caller, Capability.STATS)
    if devices.get(device_id) is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return service.device_replay(device_id, day, tz_offset_minutes)


@router.get("/mine")
def mine_stats(
    caller: IdentityRecord = Depends(require_identity),
    cert_no: str = Query(min_length=1, description="要查的学号"),
    days: int = Query(30, ge=1, le=365),
    tz_offset_minutes: int = _TZ_QUERY,
) -> MineView:
    """个人的跨设备统计。

    **不校验这个学号是不是调用方自己的**——与「登记」同一套信任模型（见 capabilities
    的「能力划分」）。`stats` 位本身就是那道门。
    """
    require_capability(caller, Capability.STATS)
    return service.mine_stats(cert_no, days, tz_offset_minutes)
