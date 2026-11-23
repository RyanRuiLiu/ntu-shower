"""电源计划域的 API 契约（hold/resume 端点与看板 holds 共用）。

响应用 TypedDict：响应不需要运行时校验，零开销——与项目「请求用 Pydantic、响应
用 TypedDict」的分工一致（见 sessions/schemas.py 的模块注释）。
"""

from enum import StrEnum

from typing_extensions import TypedDict


class HoldState(StrEnum):
    """hold 的派生状态（不落库，由 hold 三列现算）。

    - `pending`：意图已建、尚未发任何命令；取消它不碰设备。
    - `arming`：整夜布防已写入、拨钟未完成；取消会留下布防，只能等对账补完。
    - `applied`：偏置已施加；恢复必须写回墙钟。
    """

    PENDING = "pending"
    ARMING = "arming"
    APPLIED = "applied"


class DoorAssignment(TypedDict):
    """hold 施加瞬间记下的"谁在哪个喷头"。"""

    door_no: int
    cert_no: str


class HoldStatus(TypedDict):
    """hold 中的设备：派生状态、来源、施加时刻、恢复时刻、布防截止与重分配进度。

    `state` 是唯一的状态判据（见 [HoldState]），前端不再自行用 `offset_s` 是否为
    空推断。`resume_at` 只在 `applied` 时有值：恢复时刻 = 施加时刻 + 偏移。
    恢复条件两项（`past_close` 真实时间已过恢复时刻、`idle` 喷头全部空闲）逐项
    给出：两项齐了之后的一轮对账（`TICK_INTERVAL_S` 内）就会恢复。
    """

    device_id: str
    state: HoldState
    hold_source: str
    hold_started_at: str
    hold_offset_s: int | None
    resume_at: str
    arming_deadline: str
    total: int
    reassigned_count: int
    pending: list[DoorAssignment]
    past_close: bool
    idle: bool


class ResumeOutcome(StrEnum):
    """恢复常规电源计划的结果（成功族）。

    失败不走本枚举：设备离线、超时、事实缺失、正在施加、布防半施加一律抛
    problem+json，由 HTTP 状态码与 problem type 表达。
    """

    RESUMED = "resumed"  # applied：墙钟已写回并清状态
    CANCELLED = "cancelled"  # pending：意图已取消，没有写过钟
    NOT_HOLDING = "not-holding"  # 本来就不在延长中（幂等无操作）


class ResumeResult(TypedDict):
    """`POST /resume` 的成功报告：动作前状态、结果与是否真的写回了墙钟。"""

    device_id: str
    previous_state: HoldState | None
    outcome: ResumeOutcome
    clock_restored: bool


__all__ = ["DoorAssignment", "HoldState", "HoldStatus", "ResumeOutcome", "ResumeResult"]
