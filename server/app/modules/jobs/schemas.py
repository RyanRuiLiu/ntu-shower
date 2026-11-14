"""后台任务看板域的 API 契约（admin，GET /jobs/status）。

响应用 TypedDict：响应不需要运行时校验，零开销，与项目"请求用 Pydantic、
响应用 TypedDict"的分工一致（见 sessions/schemas.py 的模块注释）。

行的形状由来源模块（extend_hours.current_shifts、scheduler.snapshot）与
本文件共同钉住，tests/test_jobs.py 断言字段。
"""

from typing_extensions import TypedDict


class JobStatus(TypedDict):
    """一个后台任务的开关与名单人数（students_count）。"""

    key: str
    title: str
    enabled: bool
    students_count: int


class DoorAssignment(TypedDict):
    """延长服务记下的"谁在哪个喷头"。"""

    door_no: int
    cert_no: str


class ExtendingDevice(TypedDict):
    """正在延长服务中的设备：拨快幅度、拨快时刻、MCU 武装的截止与恢复条件。

    恢复条件两项（`past_midnight` 真实时间过午夜、`idle` 喷头全部空闲）逐项给出：
    两项齐了之后的一轮 tick（`tick_interval_s` 内）就会拨回。`pending` 是尚未放回
    的人——浴室端收回谁，谁就是走了，放回是一次性的。
    """

    device_id: str
    shift_minutes: int
    shifted_at: str
    total: int
    reassigned_count: int
    pending: list[DoorAssignment]
    mcu_off_at: str
    rearm_interval_s: int
    idle: bool
    past_midnight: bool


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

    纯只读的组合，三个部分各自来自自己的域：开关与名单是全局配置，延长中是
    `extend_hours` 的落库状态，已排定是调度器内存里的定时器。
    """

    tasks: list[JobStatus]
    extending: list[ExtendingDevice]
    scheduled: list[ScheduledAction]


__all__: list[str] = [
    "DoorAssignment",
    "ExtendingDevice",
    "JobStatus",
    "JobsStatus",
    "ScheduledAction",
]
