"""HTTP 调用审计的存储：写行、查询、保留期清理。

从 core/audit.py 拆出（2026-11 审计）：core 是最底层基础设施，核心层不该向上
依赖 auth/db/models——审计的存储归持久化层，HTTP 侧的采集与装配在
app/audit_middleware.py。两张审计表（api_call 与 command_audit）的清理共用
一个事务，见 [prune]。
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

from sqlalchemy import delete
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from ..core.logging import request_id_var, trace_id_var
from ..db.base import utcnow
from ..db.session import session_scope
from ..models.ops import ApiCall, CommandAudit
from . import audit as audit_store


class ApiCallRecord(TypedDict):
    """一行调用审计（`GET /audit/calls` 的行）。

    列即 api_call 表的列；可空字段对应"本行没有该事实"（如网关生命周期行没有
    method/path 语义、匿名调用没有 actor）。`request_body`/`response_body` 是
    JSON 体原文，数据重型响应为占位说明（见 audit_middleware.store_body）。
    """

    kind: str
    requested_at: str
    request_id: str
    trace_id: str
    actor_kind: str
    actor: str
    client_ip: str | None
    user_agent: str | None
    method: str
    path: str
    query: str | None
    request_body: str | None
    status_code: int
    duration_ms: int
    device_id: str | None
    problem_type: str | None
    detail: str | None
    response_body: str | None


def record(
    *,
    method: str,
    path: str,
    query: str = "",
    status_code: int = 0,
    duration_ms: int = 0,
    actor_kind: str = "",
    actor: str = "",
    client_ip: str = "",
    user_agent: str = "",
    request_body: str = "",
    response_body: str = "",
    device_id: str = "",
    problem_type: str = "",
    detail: str = "",
) -> None:
    """写一行 HTTP 审计（kind='http'）。关联字段取自中间件置位的 contextvar。

    与命令审计以 trace_id 关联：本行与 command_audit 各带一份，查两边即得全链路。
    `query` 是请求的查询串；`request_body` 与 `response_body` 是 JSON 体原文；
    `detail` 是失败响应的中文说明（RFC 9457 的 detail）。
    """
    with session_scope() as session:
        audit_store.insert_api_call(
            session,
            kind="http",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_ms,
            device_id=device_id,
            actor_kind=actor_kind,
            actor=actor,
            client_ip=client_ip or None,
            user_agent=user_agent or None,
            query=query or None,
            request_body=request_body or None,
            response_body=response_body or None,
            problem_type=problem_type or None,
            detail=detail or None,
            request_id=request_id_var.get(),
            trace_id=trace_id_var.get(),
        )


def list_calls(
    *,
    device_id: str | None = None,
    actor_kind: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[ApiCallRecord]:
    """按时间倒序取审计记录（/api/v1/audit/calls）。"""
    with session_scope() as session:
        return cast(
            "list[ApiCallRecord]",
            audit_store.list_api_calls(session, device_id=device_id, actor_kind=actor_kind, kind=kind, limit=limit),
        )


def prune(session: Session, retention_days: int) -> int:
    """删除超过保留期的审计行（api_call 与 command_audit），返回删除行数。

    只动审计表：业务数据（喷头状态迁移、使用会话）是运营历史，永久保留；
    库文件不复缩，页复用即可。两张表的 DELETE 共用一个事务：要么都清、
    要么都不清，两边保留期才不会错开。
    """
    cutoff = utcnow() - timedelta(days=retention_days)
    removed = 0
    for table, column in ((ApiCall, ApiCall.requested_at), (CommandAudit, CommandAudit.created_at)):
        result = session.execute(delete(table).where(column < cutoff))
        removed += int(getattr(result, "rowcount", 0) or 0)
    session.commit()
    return removed


__all__: list[str] = ["ApiCallRecord", "list_calls", "prune", "record"]
