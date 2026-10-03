"""审计领域的 API 契约。

command_audit 行的形状只有一个读者面：`GET /audit/commands`（admin）。字段与
command_audit 表的列一一对应；params/data 是入库的 JSON 原文，由调用方自行解析。
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
