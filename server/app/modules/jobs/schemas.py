"""后台任务看板域的 API 契约（admin，GET /jobs/status）。

响应用 TypedDict：响应不需要运行时校验，零开销，与项目"请求用 Pydantic、
响应用 TypedDict"的分工一致（见 sessions/schemas.py 的模块注释）。

行的形状由来源模块（power.holds、scheduler.snapshot）与本文件共同钉住，
tests/test_jobs.py 断言字段。hold 的形状（HoldStatus / DoorAssignment）住在
power/schemas.py——它是电源计划域自己的契约，看板只是转发。
"""

from typing_extensions import TypedDict

from app.modules.power.schemas import DoorAssignment, HoldStatus


class JobStatus(TypedDict):
    """一个后台任务的开关与名单人数（students_count）。"""

    key: str
    title: str
    enabled: bool
    students_count: int


class ScheduledAction(TypedDict):
    """调度器已排定的下一次动作：在等谁、等到什么时候、为什么是这一刻。

    `state` 是门现态，`started_at` 是这条判据的起算时刻（分配时刻或关阀时刻，随功能
    而定），`threshold_s` 是当时生效的阈值；`run_started_at` 与 `max_duration_s` 是
    本轮续期链的起点与上限（无上限的功能给 null）——面板据此能回答"什么时候到头"。
    """

    device_id: str
    door_no: int
    cert_no: str
    job: str
    at: str
    state: str
    session_id: int
    started_at: str
    threshold_s: int
    run_started_at: str
    max_duration_s: int | None


class JobsStatus(TypedDict):
    """后台任务的当前态（概览页看板）。

    纯只读的组合，三个部分各自来自自己的域：开关与名单是全局配置，hold 是
    电源计划域的落库状态，已排定是调度器内存里的定时器。
    """

    jobs: list[JobStatus]
    holds: list[HoldStatus]
    scheduled: list[ScheduledAction]


__all__: list[str] = [
    "DoorAssignment",
    "HoldStatus",
    "JobStatus",
    "JobsStatus",
    "ScheduledAction",
]
