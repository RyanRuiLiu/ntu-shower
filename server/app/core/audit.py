"""接口调用审计：谁、何时、对什么、结果如何，一行一个调用。

标准依据见 docs/decisions/0037-observability.md（OWASP Logging Cheat Sheet 的
事件清单、NIST SP 800-92 的生成/保护/保留框架、W3C Trace Context 的关联标识）。

## 记什么

- 全部 API 请求（/api、/ops、/ota）：认证失败（401）、授权失败（403）、参数不合法
  （422）、服务端错误（5xx）天然落在同一张表——OWASP 要求必记的事件类
- 设备网关生命周期（连接/断开）：设备何时在线是回溯一条命令的上下文

## 不记什么（OWASP：禁止记录密钥与敏感个人信息）

- 请求体与响应体：学号密码、业务参数、令牌一律不落库；path + status 已能回答
  "谁在什么时候对哪台设备做了什么、成没成"
- 静态资源与 /healthz：无安全语义的噪音

## 关联

- `request_id`：本请求的关联键，随 `X-Request-ID` 响应头返回
- `trace_id`：W3C Trace Context 的 trace-id——调用方带 `traceparent` 就沿用，
  否则生成；HTTP 审计与 `command_audit`（命令审计）借此互通
- `actor`：ops 令牌只记指纹，绝不记令牌本身；身份标识脱敏后记录

## 保留（NIST SP 800-92：按运维需要定保留期并执行）

`audit_retention_days`（默认 90 天），过期行由每日清理任务删除。
"""

import asyncio
import hashlib
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import db
from .clock import now_rfc3339
from .config import settings
from .logging import log_event, request_id_var, trace_id_var
from .protocol import JsonObject


def exc_info_text() -> str:
    """当前异常的单行摘要：给结构化日志的 error 属性，多行堆栈压成一行可检索。"""
    return traceback.format_exc(limit=8).replace("\n", " | ")


#: 需要审计的路径前缀。静态资源与 /healthz 不审计：无安全语义的噪音。
_AUDITED_PREFIXES = ("/api", "/ops", "/ota")

_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$", re.IGNORECASE)

_PRUNE_INTERVAL_S = 6 * 3600.0

_prune_task: asyncio.Task[None] | None = None


def init_tables() -> None:
    db.get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS api_call (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            requested_at TEXT    NOT NULL,
            request_id   TEXT    NOT NULL,
            trace_id     TEXT    NOT NULL,
            actor_kind   TEXT    NOT NULL,
            actor        TEXT    NOT NULL,
            method       TEXT    NOT NULL,
            path         TEXT    NOT NULL,
            status_code  INTEGER NOT NULL,
            duration_ms  INTEGER NOT NULL,
            device_id    TEXT,
            problem_type TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_api_call_requested
            ON api_call(requested_at);
        CREATE INDEX IF NOT EXISTS idx_api_call_device
            ON api_call(device_id, requested_at);
        """
    )


def resolve_trace_id(traceparent: str | None) -> str:
    """取 W3C trace-id：调用方带合法 `traceparent` 就沿用它的 trace-id，否则生成。

    只取 trace-id 一段：span-id 是调用方那次调用的，与"服务端这一行审计"不是一回事。
    """
    if traceparent:
        match = _TRACEPARENT.match(traceparent.strip())
        if match:
            return match.group(1).lower()
    return uuid.uuid4().hex


def mask_actor(actor: str) -> str:
    """标识脱敏：日志与审计都不写全量标识——它可直接用于冒充。"""
    if len(actor) <= 8:
        return "***"
    return f"{actor[:4]}...{actor[-4:]}"


def record(
    *,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    actor_kind: str,
    actor: str,
    device_id: str = "",
    problem_type: str = "",
) -> None:
    """写一行审计。关联字段取自中间件置位的 contextvar；同步执行（经线程池调用）。

    与命令审计的关联靠 trace_id：本行与 command_audit 各带一份，查两边即得全链路。
    """
    db.get_conn().execute(
        "INSERT INTO api_call (requested_at, request_id, trace_id, actor_kind, actor,"
        " method, path, status_code, duration_ms, device_id, problem_type)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            now_rfc3339(),
            request_id_var.get(),
            trace_id_var.get(),
            actor_kind,
            actor,
            method,
            path,
            status_code,
            duration_ms,
            device_id or None,
            problem_type or None,
        ),
    )
    db.get_conn().commit()


def record_gateway(device_id: str, event: str) -> None:
    """网关生命周期一行（连接/断开）。method 固定 WS，status 约定 0=在线 1=离线。"""
    status = 0 if event == "connect" else 1
    db.get_conn().execute(
        "INSERT INTO api_call (requested_at, request_id, trace_id, actor_kind, actor,"
        " method, path, status_code, duration_ms, device_id, problem_type)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            now_rfc3339(),
            uuid.uuid4().hex,
            "",
            "device",
            device_id,
            "WS",
            f"/gateway/device {event}",
            status,
            0,
            device_id,
            None,
        ),
    )
    db.get_conn().commit()


def list_calls(
    *,
    device_id: str | None = None,
    actor_kind: str | None = None,
    limit: int = 100,
) -> list[JsonObject]:
    """按时间倒序取审计记录（/ops/audit）。"""
    sql = (
        "SELECT requested_at, request_id, trace_id, actor_kind, actor, method, path,"
        " status_code, duration_ms, device_id, problem_type FROM api_call"
    )
    params: list[object] = []
    clauses: list[str] = []
    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    if actor_kind:
        clauses.append("actor_kind = ?")
        params.append(actor_kind)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.get_conn().execute(sql, params).fetchall()]


def prune(retention_days: int) -> int:
    """删除超过保留期的审计行，返回删除行数。库文件不复缩，页复用即可。"""
    cursor = db.get_conn().execute(
        "DELETE FROM api_call WHERE requested_at < datetime('now', ?)",
        (f"-{retention_days} days",),
    )
    db.get_conn().commit()
    return cursor.rowcount


async def _prune_loop() -> None:
    while True:
        try:
            removed = prune(settings.audit_retention_days)
            if removed:
                log_event(
                    "INFO",
                    "audit.pruned",
                    "审计过期行已清理",
                    rows=removed,
                    retention_days=settings.audit_retention_days,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log_event(
                "ERROR",
                "audit.prune-failed",
                "审计清理失败，等下一轮重试",
                error=exc_info_text(),
            )
        await asyncio.sleep(_PRUNE_INTERVAL_S)


def start_prune() -> None:
    global _prune_task
    if _prune_task is None or _prune_task.done():
        _prune_task = asyncio.create_task(_prune_loop())


def stop_prune() -> None:
    global _prune_task
    if _prune_task is not None:
        _prune_task.cancel()
        _prune_task = None


def _actor_of(request: Request) -> tuple[str, str]:
    """从请求头判定调用方。令牌只落指纹（sha256 前 16 位），绝不落令牌本身。"""
    token = request.headers.get("X-Ops-Token")
    if token:
        return "ops", hashlib.sha256(token.encode()).hexdigest()[:16]
    identity = request.headers.get("X-Identity")
    if identity:
        return "identity", mask_actor(identity)
    return "anonymous", ""


def _device_of(path: str) -> str:
    """路径里可识别的设备（/ops/devices/{id}/...）。"""
    parts = path.split("/")
    if "devices" in parts:
        index = parts.index("devices")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def install_audit(app: FastAPI) -> None:
    """装配审计中间件：一行一个 API 调用 + X-Request-ID 响应头。

    写库经线程池走"每线程连接"（见 core.db），不在事件循环里做同步 IO。
    WebSocket 不经过 http 中间件，网关生命周期由 gateway 显式调 record_gateway。
    """

    class AuditMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self,
            request: Request,
            call_next: Callable[[Request], Awaitable[Response]],
        ) -> Response:
            path = request.url.path
            if not path.startswith(_AUDITED_PREFIXES):
                return await call_next(request)

            request_id = uuid.uuid4().hex
            trace_id = resolve_trace_id(request.headers.get("traceparent"))
            request_id_var.set(request_id)
            trace_id_var.set(trace_id)
            started = time.perf_counter()
            response = await call_next(request)
            duration_ms = int((time.perf_counter() - started) * 1000)
            response.headers["X-Request-ID"] = request_id

            actor_kind, actor = _actor_of(request)
            await run_in_threadpool(
                record,
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                actor_kind=actor_kind,
                actor=actor,
                device_id=_device_of(path),
                problem_type=response.headers.get("X-Problem-Type", ""),
            )
            return response

    app.add_middleware(AuditMiddleware)
