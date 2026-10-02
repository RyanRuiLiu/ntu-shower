"""接口调用审计：谁、何时、对什么、结果如何，一行一个调用。

按 OWASP Logging Cheat Sheet 的事件清单与 NIST SP 800-92 的保留框架设计，
标准依据见 docs/DECISIONS.md「可观测性」。覆盖 /api、/ops、/ota 的全部请求
（认证、授权、参数与服务端错误天然落表）与设备网关生命周期；静态资源与
/healthz 不审计。

JSON 请求体与响应体原文入库，全有或全无、不截断、不脱敏；数据重型且可重取的
截图、拍照、UI 节点树与 multipart 上传体除外。审计行按 audit_retention_days
保留，过期行由清理任务删除；业务数据永久保留。
"""

import asyncio
import contextlib
import json
import re
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from typing import cast
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from . import db
from .clock import now_rfc3339
from .config import settings
from .logging import log_event, request_id_var, trace_id_var
from .protocol import JsonObject


def exc_info_text() -> str:
    """当前异常的单行摘要：给结构化日志的 error 属性，多行堆栈压成一行可检索。"""
    return traceback.format_exc(limit=8).replace("\n", " | ")


#: 需要审计的路径前缀 = 版本化对外面的全部域（/api/v1，见 main.py 的模块注释）。
#: /ota/manifest 是设备硬编码契约，无版本前缀（见 ota/routes.py），但它是设备
#: 最频繁的一手活动痕迹，单独列入。静态资源与 /healthz 不审计：无安全语义的
#: 噪音。新增域必须同步此表，否则该域的调用（含 401/403）不留痕——test_audit.py
#: 有跨域覆盖钉住它。
_AUDITED_PREFIXES = ("/api/v1", "/ota/manifest")

_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$", re.IGNORECASE)

_PRUNE_INTERVAL_S = 6 * 3600.0

_prune_task: asyncio.Task[None] | None = None


def init_tables() -> None:
    db.get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS api_call (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT    NOT NULL,
            requested_at  TEXT    NOT NULL,
            request_id    TEXT    NOT NULL,
            trace_id      TEXT    NOT NULL,
            actor_kind    TEXT    NOT NULL,
            actor         TEXT    NOT NULL,
            client_ip     TEXT,
            user_agent    TEXT,
            method        TEXT    NOT NULL,
            path          TEXT    NOT NULL,
            query         TEXT,
            request_body  TEXT,
            status_code   INTEGER NOT NULL,
            duration_ms   INTEGER NOT NULL,
            device_id     TEXT,
            problem_type  TEXT,
            detail        TEXT,
            response_body TEXT
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


def _actor_of(request: Request) -> tuple[str, str]:
    """从请求头判定调用方，如实记录原文、不脱敏。"""
    identity = request.headers.get("X-Identity")
    if identity:
        return "identity", identity
    return "anonymous", ""


#: 请求体与响应体入库的体积上限。全有或全无，绝不截断：截断了后续就查不回真实
#: 内容，超限的记录占位说明（媒体类响应可重取，与不落媒体的取舍一致）。普通
#: JSON 请求与响应都在 KB 量级，全文入库。
BODY_MAX_BYTES = 256 * 1024

#: 响应体不落库的路径：数据重型且可重取的截图、拍照、UI 节点树三个端点。
_DATA_HEAVY_PATH = re.compile(r"/(capture|dump|photo)$")


def store_body(body: str) -> str:
    """体积在限内原文返回，超限返回占位说明，绝不截断。"""
    if len(body.encode()) <= BODY_MAX_BYTES:
        return body
    return f"[JSON 体 {len(body.encode())} 字节，超出 {BODY_MAX_BYTES} 字节上限未存储]"


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
    db.write(
        "INSERT INTO api_call (kind, requested_at, request_id, trace_id, actor_kind, actor,"
        " client_ip, user_agent, method, path, query, request_body, status_code, duration_ms,"
        " device_id, problem_type, detail, response_body)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "http",
            now_rfc3339(),
            request_id_var.get(),
            trace_id_var.get(),
            actor_kind,
            actor,
            client_ip or None,
            user_agent or None,
            method,
            path,
            query or None,
            request_body or None,
            status_code,
            duration_ms,
            device_id or None,
            problem_type or None,
            detail or None,
            store_body(response_body) or None,
        ),
    )


def record_gateway(device_id: str, event: str, client_ip: str = "") -> None:
    """网关生命周期一行（connect/disconnect）。kind='gateway'，status 约定 0=连接 1=断开。"""
    status_code = 0 if event == "connect" else 1
    db.write(
        "INSERT INTO api_call (kind, requested_at, request_id, trace_id, actor_kind, actor,"
        " client_ip, user_agent, method, path, query, request_body, status_code, duration_ms,"
        " device_id, problem_type, detail)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "gateway",
            now_rfc3339(),
            uuid.uuid4().hex,
            "",
            "device",
            device_id,
            client_ip or None,
            None,
            "WS",
            "/gateway/device",
            None,
            None,
            status_code,
            0,
            device_id,
            None,
            None,
        ),
    )


def list_calls(
    *,
    device_id: str | None = None,
    actor_kind: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[JsonObject]:
    """按时间倒序取审计记录（/ops/audit）。"""
    sql = (
        "SELECT kind, requested_at, request_id, trace_id, actor_kind, actor, client_ip,"
        " user_agent, method, path, query, request_body, status_code, duration_ms, device_id,"
        " problem_type, detail, response_body FROM api_call"
    )
    params: list[object] = []
    clauses: list[str] = []
    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    if actor_kind:
        clauses.append("actor_kind = ?")
        params.append(actor_kind)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in db.get_conn().execute(sql, params).fetchall()]


def prune(retention_days: int) -> int:
    """删除超过保留期的审计行（api_call 与 command_audit），返回删除行数。

    只动审计表：业务数据（喷头状态迁移、使用会话）是运营历史，永久保留；
    库文件不复缩，页复用即可。两张表的 DELETE 共用一个事务（不走 db.write 的
    单语句事务）：要么都清、要么都不清，两边保留期才不会错开。
    """
    conn = db.get_conn()
    removed = 0
    for table, column in (("api_call", "requested_at"), ("command_audit", "created_at")):
        cursor = conn.execute(f"DELETE FROM {table} WHERE {column} < datetime('now', ?)", (f"-{retention_days} days",))
        removed += cursor.rowcount
    conn.commit()
    return removed


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
        except Exception:  # noqa: BLE001 清理失败不能带垮服务，下一轮重试
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


def _device_of(path: str, query: str) -> str:
    """本次调用作用于哪台设备就记哪台。解析优先级：

    ① 路径段 `/devices/{id}/...`（设备动作与档案）；
    ② query 的 `device_id`（如 /doors?device_id=）。
    都没有就为空——调用不针对任何设备（如配置、名单本身）。
    """
    parts = path.split("/")
    if "devices" in parts:
        index = parts.index("devices")
        if index + 1 < len(parts):
            return parts[index + 1]
    if query:
        value = parse_qs(query).get("device_id", [""])[0]
        if value:
            return value
    return ""


def install_audit(app: FastAPI) -> None:
    """装配审计中间件：一行一个 API 调用，并回填 X-Request-ID 响应头。

    写库经线程池走每线程连接（见 core.db），不在事件循环里做同步 IO。
    WebSocket 不经过 HTTP 中间件，网关生命周期由 gateway 调 record_gateway 记录。
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

            # 请求体原文落库。只对 JSON 体做：multipart（APK 上传）本体已存
            # ota_releases，审计行记版本与大小即完整事实。按 "json" 判定而非全等：
            # application/problem+json 也含 json，而与 application/json 全等反而
            # 匹配不上它。读完放回，下游照常解析。
            request_body = ""
            content_type = request.headers.get("content-type", "")
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and "json" in content_type:
                body_bytes = await request.body()

                async def receive() -> dict[str, object]:
                    return {"type": "http.request", "body": body_bytes, "more_body": False}

                request = Request(request.scope, receive=receive)
                request_body = store_body(body_bytes.decode(errors="replace"))

            started = time.perf_counter()
            response = await call_next(request)
            duration_ms = int((time.perf_counter() - started) * 1000)
            response.headers["X-Request-ID"] = request_id

            # JSON 响应体全文入库。缓冲响应：处理器产出的体量在 KB 量级，MB 级的
            # 媒体 base64 由 store_body 的上限挡下；problem 响应顺带取出 detail。
            # 截图、拍照、UI 树三个端点的响应不落库（见 _DATA_HEAVY_PATH）。
            response_body = ""
            detail = ""
            if "json" in response.headers.get("content-type", "") and not _DATA_HEAVY_PATH.search(path):
                # BaseHTTPMiddleware 返回的实际是流式响应子类（Starlette 约定）
                streaming = cast(StreamingResponse, response)
                chunks: list[bytes] = []
                async for chunk in streaming.body_iterator:
                    if isinstance(chunk, (bytes, bytearray, memoryview)):
                        chunks.append(bytes(chunk))
                    else:
                        chunks.append(str(chunk).encode())
                body = b"".join(chunks)
                response = Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
                response_body = store_body(body.decode(errors="replace"))
                if response.headers.get("X-Problem-Type"):
                    with contextlib.suppress(Exception):
                        detail = str(json.loads(body).get("detail") or "")

            # 来源 IP：经 nginx 代理而来，真实地址在 X-Real-IP 里
            client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
            actor_kind, actor = _actor_of(request)
            await run_in_threadpool(
                record,
                method=request.method,
                path=path,
                query=request.url.query,
                status_code=response.status_code,
                duration_ms=duration_ms,
                actor_kind=actor_kind,
                actor=actor,
                client_ip=client_ip,
                user_agent=request.headers.get("user-agent", ""),
                request_body=request_body,
                response_body=response_body,
                device_id=_device_of(path, request.url.query),
                problem_type=response.headers.get("X-Problem-Type", ""),
                detail=detail,
            )
            return response

    app.add_middleware(AuditMiddleware)
