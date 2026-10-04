"""审计领域的 API 契约。

两张表的行形状各只有一个读者面：command_audit → `GET /audit/commands`、
background_task_run → `GET /audit/background-task-runs`（都是 admin）。字段与表列
一一对应；params/data 是入库的 JSON 原文，由调用方自行解析。
"""

from typing_extensions import TypedDict


class CommandAuditRecord(TypedDict):
    """一行命令审计：下发、参数与设备回报（与 /audit/calls 以 trace_id 关联）。

    `finished_at`/`status`/`reason_code`/`terminal_message`/`data` 为空（None）
    表示命令尚未收尾——它还在等设备回报或已等待超时。
    """

    command_id: str
    device_id: str
    action: str
    params: str | None
    created_at: str
    finished_at: str | None
    status: str | None
    reason_code: str | None
    terminal_message: str | None
    trace_id: str | None
    data: str | None


class BackgroundTaskRun(TypedDict):
    """一行后台任务记录：配置好的任务自动替谁做了什么、为什么。

    `reason` 是没动手时的中文原因（"不在名单里"这类）；`threshold_s` 是当时生效的
    阈值——存进这一行，是为了事后不必去找当时的配置就能看懂"为什么是这一刻"。
    `occurred_at` 是服务端轴：与 `command_audit.created_at` 对照即知这次动作发了哪条命令。
    """

    id: int
    occurred_at: str
    device_id: str
    door_no: int
    session_id: int | None
    cert_no: str
    feature: str
    action: str | None
    outcome: str
    reason: str | None
    threshold_s: int | None
