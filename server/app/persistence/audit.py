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

from ..contracts import CommandAuditRecord, JobRun
from ..core import outcomes, reason_codes
from ..core.clock import format_rfc3339
from ..db.base import utcnow
from ..models.enums import ActorKind, CommandStatus, JobOutcome
from ..models.identity import Device
from ..models.ops import ApiCall, CommandAudit
from ..models.ops import JobRun as JobRunRow


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
    session.add(
        CommandAudit(
            command_id=command_id,
            device_id=_device_pk(session, device_id),
            device_key=device_id,
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
    statement = select(CommandAudit).order_by(CommandAudit.id.desc()).limit(limit)
    if device_id:
        statement = statement.where(CommandAudit.device_key == device_id)
    records: list[CommandAuditRecord] = []
    for row in session.scalars(statement).all():
        records.append(
            {
                "command_id": row.command_id,
                "device_id": row.device_key,
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


def insert_job_run(
    session: Session,
    device_id: str,
    door_no: int,
    cert_no: str,
    job: str,
    outcome: JobOutcome,
    occurred_at: datetime,
    *,
    action: str | None = None,
    session_id: int | None = None,
    reason: str = "",
    threshold_s: int | None = None,
) -> None:
    session.add(
        JobRunRow(
            occurred_at=occurred_at,
            device_id=_device_pk(session, device_id),
            device_key=device_id,
            door_no=door_no,
            session_id=session_id,
            cert_no=cert_no,
            job=job,
            action=action,
            outcome=outcome,
            reason=reason or None,
            threshold_s=threshold_s,
        )
    )
    session.commit()


def list_job_runs(session: Session, device_id: str | None = None, limit: int = 50) -> list[JobRun]:
    statement = select(JobRunRow).order_by(JobRunRow.id.desc()).limit(limit)
    if device_id:
        statement = statement.where(JobRunRow.device_key == device_id)
    records: list[JobRun] = []
    for row in session.scalars(statement).all():
        records.append(
            {
                "id": int(row.id),
                "occurred_at": format_rfc3339(row.occurred_at),
                "device_id": row.device_key,
                "door_no": int(row.door_no),
                "session_id": row.session_id,
                "cert_no": row.cert_no,
                "job": row.job,
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
    "insert_api_call",
    "insert_command",
    "insert_job_run",
    "list_api_calls",
    "list_commands",
    "list_job_runs",
]


def insert_api_call(
    session: Session,
    *,
    kind: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    device_id: str = "",
    actor_kind: str = "",
    actor: str = "",
    client_ip: str | None = None,
    user_agent: str | None = None,
    query: str | None = None,
    request_body: str | None = None,
    response_body: str | None = None,
    problem_type: str | None = None,
    detail: str | None = None,
    request_id: str = "",
    trace_id: str = "",
) -> None:
    """写一行 api_calls 审计（HTTP 请求与网关生命周期共用）。

    device_id 是文本业务键，这里转整数外键；设备不在册时行仍要落（审计先于档案），
    外键列留空。
    """
    device_pk = _device_pk(session, device_id) if device_id else None
    requested_at = utcnow()
    session.add(
        ApiCall(
            kind=kind,
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            device_id=device_pk,
            device_key=device_id,
            actor_kind=ActorKind(actor_kind) if actor_kind else ActorKind.ANONYMOUS,
            actor=actor,
            client_ip=client_ip,
            user_agent=user_agent,
            query=query,
            request_body=request_body,
            response_body=response_body,
            problem_type=problem_type,
            detail=detail,
            request_id=request_id,
            trace_id=trace_id,
            requested_at=requested_at,
        )
    )
    session.commit()
    _ = format_rfc3339


def list_api_calls(
    session: Session,
    *,
    device_id: str | None = None,
    actor_kind: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """按时间倒序取审计行（/api/v1/audit/calls）。

    返回形状与旧表一致：device_id 是文本业务键（联表取），requested_at 是 RFC 3339。
    """
    stmt = select(ApiCall).order_by(ApiCall.id.desc()).limit(limit)
    if device_id:
        stmt = stmt.where(ApiCall.device_key == device_id)
    if actor_kind:
        stmt = stmt.where(ApiCall.actor_kind == actor_kind)
    if kind:
        stmt = stmt.where(ApiCall.kind == kind)
    rows = session.scalars(stmt).all()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "kind": row.kind,
                "requested_at": format_rfc3339(row.requested_at),
                "request_id": row.request_id,
                "trace_id": row.trace_id,
                "actor_kind": str(row.actor_kind),
                "actor": row.actor,
                "client_ip": row.client_ip,
                "user_agent": row.user_agent,
                "method": row.method,
                "path": row.path,
                "query": row.query,
                "request_body": row.request_body,
                "status_code": row.status_code,
                "duration_ms": row.duration_ms,
                "device_id": row.device_key,
                "problem_type": row.problem_type,
                "detail": row.detail,
                "response_body": row.response_body,
            }
        )
    return out
