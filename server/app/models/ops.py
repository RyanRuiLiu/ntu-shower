"""运维域：访问日志、命令审计、设备日志、链路事件、后台任务。

全部是只追加的时间序列，因此每条都有 `occurred_at` 一类的时刻列和对应的索引；
保留策略按表分别设定，不由数据库自己决定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, enum_column, utcnow
from .enums import ActorKind, CommandStatus, DeviceLogKind, JobOutcome, LinkEvent


class ApiCall(Base):
    """一次 HTTP 调用的审计行，保留 90 天。"""

    __tablename__ = "api_calls"
    __table_args__ = (Index("ix_api_calls_device_id_requested_at", "device_id", "requested_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
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


class DeviceLog(Base):
    """设备上送的日志，保留 30 天。"""

    __tablename__ = "device_logs"
    __table_args__ = (Index("ix_device_logs_device_id_kind_uploaded_at", "device_id", "kind", "uploaded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[DeviceLogKind] = mapped_column(enum_column(DeviceLogKind, "device_log_kind"))
    content: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class DeviceLinkEvent(Base):
    """设备上下线事件，保留 90 天。"""

    __tablename__ = "device_link_events"
    __table_args__ = (Index("ix_device_link_events_device_id_occurred_at", "device_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    event: Mapped[LinkEvent] = mapped_column(enum_column(LinkEvent, "link_event"))
    reason: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


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


__all__: list[str] = [
    "ApiCall",
    "CommandAudit",
    "DeviceLinkEvent",
    "DeviceLog",
    "JobRun",
]
