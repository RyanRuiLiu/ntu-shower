"""电源计划域的 API 契约（hold/resume 端点与看板 holds 共用）。

响应用 TypedDict：响应不需要运行时校验，零开销——与项目「请求用 Pydantic、响应
用 TypedDict」的分工一致（见 sessions/schemas.py 的模块注释）。
"""

from typing_extensions import TypedDict


class DoorAssignment(TypedDict):
    """hold 施加瞬间记下的"谁在哪个喷头"。"""

    door_no: int
    cert_no: str


class HoldStatus(TypedDict):
    """hold 中的设备：来源、建立时刻、租约到点、放回进度与恢复条件两项。

    恢复条件两项（`past_close` 真实时间已过被跳过的 close、`idle` 喷头全部空闲）
    逐项给出：两项齐了之后的一轮对账（`TICK_INTERVAL_S` 内）就会恢复。
    """

    device_id: str
    hold_source: str
    hold_started_at: str
    hold_offset_s: int | None
    lease_deadline: str
    total: int
    reassigned_count: int
    pending: list[DoorAssignment]
    past_close: bool
    idle: bool


class PowerActionResult(TypedDict):
    """hold / resume 动作的返回：设备标识与是否已处于恢复后状态。"""

    device_id: str
    resumed: bool


__all__ = ["DoorAssignment", "HoldStatus", "PowerActionResult"]
