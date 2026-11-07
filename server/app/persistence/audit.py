"""命令审计与后台任务记录。

契约与旧实现一致：只记终局、按时间倒序、`params` / `data` 以 JSON 字符串回给调用方。
设备标识通过关联取回业务字符串——整数主键不外泄。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import outcomes, reason_codes
from ..core.clock import format_rfc3339
from ..models.enums import BackgroundOutcome, CommandStatus
from ..models.identity import Device
from ..models.ops import BackgroundTaskRun as BackgroundTaskRunRow
from ..models.ops import CommandAudit
from ..modules.audit.schemas import BackgroundTaskRun, CommandAuditRecord


def _device_pk(session: Session, device_id: str) -> int | None:
    return session.scalar(select(Device.id).where(Device.device_id == device_id))


def _dumps(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def insert_command(
    session: Session,
    command_id: str,
    device_id: str,
    action: str,
    created_at: datetime,
    trace_id: str = "",
    params: dict[str, Any] | None = None,
) -> None:
    device_pk = _device_pk(session, device_id)
    if device_pk is None:
        return
    session.add(
        CommandAudit(
            command_id=command_id,
            device_id=device_pk,
            action=action,
            created_at=created_at,
            trace_id=trace_id,
            params=params,
        )
    )
    session.commit()


def finish(
    session: Session,
    command_id: str,
    status: CommandStatus,
    reason_code: str,
    terminal_message: str,
    finished_at: datetime,
    data: dict[str, Any] | None = None,
) -> None:
    row = session.scalars(select(CommandAudit).where(CommandAudit.command_id == command_id)).one_or_none()
    if row is None:
        return
    row.finished_at = finished_at
    row.status = status
    row.reason_code = reason_code
    row.terminal_message = terminal_message
    row.result = data
    session.commit()


def abort_pending(session: Session, now: datetime) -> int:
    """启动时收尾上一进程留下的 pending 命令，返回受影响行数。"""
    rows = session.scalars(select(CommandAudit).where(CommandAudit.status.is_(None))).all()
    for row in rows:
        row.finished_at = now
        row.status = CommandStatus(outcomes.FAILED.value)
        row.reason_code = reason_codes.COMMAND_ABORTED.code
        row.terminal_message = "服务重启，命令未收尾"
    session.commit()
    return len(rows)


def list_commands(session: Session, device_id: str | None = None, limit: int = 50) -> list[CommandAuditRecord]:
    statement = (
        select(CommandAudit, Device.device_id)
        .outerjoin(Device, CommandAudit.device_id == Device.id)
        .order_by(CommandAudit.id.desc())
        .limit(limit)
    )
    if device_id:
        statement = statement.where(Device.device_id == device_id)
    records: list[CommandAuditRecord] = []
    for row, business_id in session.execute(statement).all():
        records.append(
            {
                "command_id": row.command_id,
                "device_id": business_id or "",
                "action": row.action,
                "params": _dumps(row.params),
                "created_at": format_rfc3339(row.created_at),
                "finished_at": format_rfc3339(row.finished_at) if row.finished_at else None,
                "status": str(row.status) if row.status else None,
                "reason_code": row.reason_code,
                "terminal_message": row.terminal_message,
                "trace_id": row.trace_id or None,
                "data": _dumps(row.result),
            }
        )
    return records


def insert_background_task_run(
    session: Session,
    device_id: str,
    door_no: int,
    cert_no: str,
    feature: str,
    outcome: BackgroundOutcome,
    occurred_at: datetime,
    *,
    action: str | None = None,
    session_id: int | None = None,
    reason: str = "",
    threshold_s: int | None = None,
) -> None:
    device_pk = _device_pk(session, device_id)
    if device_pk is None:
        return
    session.add(
        BackgroundTaskRunRow(
            occurred_at=occurred_at,
            device_id=device_pk,
            door_no=door_no,
            session_id=session_id,
            cert_no=cert_no,
            feature=feature,
            action=action,
            outcome=outcome,
            reason=reason or None,
            threshold_s=threshold_s,
        )
    )
    session.commit()


def list_background_task_runs(
    session: Session, device_id: str | None = None, limit: int = 50
) -> list[BackgroundTaskRun]:
    statement = (
        select(BackgroundTaskRunRow, Device.device_id)
        .outerjoin(Device, BackgroundTaskRunRow.device_id == Device.id)
        .order_by(BackgroundTaskRunRow.id.desc())
        .limit(limit)
    )
    if device_id:
        statement = statement.where(Device.device_id == device_id)
    records: list[BackgroundTaskRun] = []
    for row, business_id in session.execute(statement).all():
        records.append(
            {
                "id": int(row.id),
                "occurred_at": format_rfc3339(row.occurred_at),
                "device_id": business_id or "",
                "door_no": int(row.door_no),
                "session_id": row.session_id,
                "cert_no": row.cert_no,
                "feature": row.feature,
                "action": row.action,
                "outcome": str(row.outcome),
                "reason": row.reason,
                "threshold_s": row.threshold_s,
            }
        )
    return records


def count_pending(session: Session) -> int:
    """未收尾的命令数，供启动自检与测试使用。"""
    return int(session.scalar(select(func.count()).select_from(CommandAudit).where(CommandAudit.status.is_(None))) or 0)


__all__: list[str] = [
    "abort_pending",
    "count_pending",
    "finish",
    "insert_background_task_run",
    "insert_command",
    "list_background_task_runs",
    "list_commands",
]
