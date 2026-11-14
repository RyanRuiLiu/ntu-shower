"""审计横切层：api_call、command_audit、job_run 三张表的模型与存取。

审计是横切基础设施，不是业务模块：写入方是全部模块与网关生命周期，读取方是
audit 域的只读出口。三张表都是只追加的时间序列，因此每条都有时刻列与索引；
保留策略按表分别设定，不由数据库自己决定（见 api_calls.RETENTIONS）。

JobRun 的 session_id 外键指向 usage_sessions（字符串表名，不需要 import 会话模型）：
审计行引用业务行，业务行不反向依赖审计层。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.contracts import ActorKind, ApiCallKind, CommandAuditRecord, CommandStatus, JobOutcome
from app.contracts import JobRun as JobRunView
from app.core import outcomes, reason_codes
from app.core.clock import format_rfc3339
from app.db.base import Base, UtcDateTime, enum_column, utcnow


class ApiCall(Base):
    """一次 HTTP 调用的审计行，保留 90 天。"""

    __tablename__ = "api_calls"
    __table_args__ = (Index("ix_api_calls_device_id_requested_at", "device_id", "requested_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ApiCallKind] = mapped_column(enum_column(ApiCallKind, "api_call_kind"))
    requested_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    actor_kind: Mapped[ActorKind] = mapped_column(enum_column(ActorKind, "actor_kind"))
    actor: Mapped[str] = mapped_column(String(64), default="")
    client_ip: Mapped[str | None] = mapped_column(String(45), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    query: Mapped[str | None] = mapped_column(String(512), default=None)
    #: 原文，不解析：非 JSON 请求体也要能存
    request_body: Mapped[str | None] = mapped_column(Text, default=None)
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[str | None] = mapped_column(String(64), default=None)
    problem_type: Mapped[str | None] = mapped_column(String(64), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    response_body: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CommandAudit(Base):
    """下发给设备的命令及其终态，保留 180 天。"""

    __tablename__ = "command_audits"
    __table_args__ = (Index("ix_command_audits_device_id_created_at", "device_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 端到端幂等身份，重投不会产生第二条
    command_id: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[str | None] = mapped_column(String(64), default=None)
    action: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    status: Mapped[CommandStatus | None] = mapped_column(enum_column(CommandStatus, "command_status"), default=None)
    reason_code: Mapped[str | None] = mapped_column(String(64), default=None)
    terminal_message: Mapped[str | None] = mapped_column(Text, default=None)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    #: 设备在成功时回传的数据，字段名由 core.outcomes.ACTION_DATA 登记
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


class JobRun(Base):
    """后台任务的一次执行记录，保留 180 天。"""

    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_device_id_occurred_at", "device_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), default=None)
    door_no: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("usage_sessions.id", ondelete="SET NULL"), default=None)
    cert_no: Mapped[str] = mapped_column(String(32), default="")
    job: Mapped[str] = mapped_column(String(32))
    action: Mapped[str | None] = mapped_column(String(32), default=None)
    outcome: Mapped[JobOutcome] = mapped_column(enum_column(JobOutcome, "job_outcome"))
    reason: Mapped[str | None] = mapped_column(String(128), default=None)
    threshold_s: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


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
            device_id=device_id or None,
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
        statement = statement.where(CommandAudit.device_id == device_id)
    records: list[CommandAuditRecord] = []
    for row in session.scalars(statement).all():
        records.append(
            {
                "command_id": row.command_id,
                "device_id": row.device_id,
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
        JobRun(
            occurred_at=occurred_at,
            device_id=device_id or None,
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


def list_job_runs(session: Session, device_id: str | None = None, limit: int = 50) -> list[JobRunView]:
    statement = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
    if device_id:
        statement = statement.where(JobRun.device_id == device_id)
    records: list[JobRunView] = []
    for row in session.scalars(statement).all():
        records.append(
            {
                "id": int(row.id),
                "occurred_at": format_rfc3339(row.occurred_at),
                "device_id": row.device_id,
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
    kind: ApiCallKind,
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
    requested_at = utcnow()
    session.add(
        ApiCall(
            kind=kind,
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            device_id=device_id or None,
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
        stmt = stmt.where(ApiCall.device_id == device_id)
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
                "device_id": row.device_id,
                "problem_type": row.problem_type,
                "detail": row.detail,
                "response_body": row.response_body,
            }
        )
    return out
