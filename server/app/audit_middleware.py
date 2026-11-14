"""HTTP 审计的采集与装配：谁、何时、对什么、结果如何，一行一个调用。

按 OWASP Logging Cheat Sheet 的事件清单与 NIST SP 800-92 的保留框架设计，
标准依据见 docs/adr/0014-observability.md。覆盖 /api/v1 与 /ota/manifest 的全部
请求（认证、授权、参数与服务端错误天然落表）与设备网关生命周期；静态资源与
/healthz 不审计。

JSON 请求体与响应体原文入库，全有或全无、不截断、不脱敏（受限审计表；stdout 日志
不写这些字段）；数据重型且可重取的截图、拍照、UI 节点树与 multipart 上传体除外。
审计行按 audit_retention_days 保留，过期行由清理任务删除；业务数据永久保留。

存储在 persistence/api_calls.py（2026-11 审计拆分）：采集归 HTTP 侧，落库归
持久化层——core 保持零向上依赖。
"""

import contextlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import parse_qs

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from app.contracts import ApiCallKind
from app.core.config import settings
from app.core.logging import log_event, request_id_var, trace_id_var
from app.db.session import session_scope
from app.modules.devices.models import DeviceLinkEvent
from app.modules.doors.models import DoorSnapshot
from app.modules.voice.models import VoiceDelivery
from app.persistence import api_calls
from app.persistence.audit import ApiCall, CommandAudit, JobRun

#: 需要审计的路径前缀 = 版本化对外面的全部域（/api/v1，见 main.py 的模块注释）。
#: /ota/manifest 是设备硬编码契约，无版本前缀（见 ota/routes.py），但它是设备
#: 最频繁的一手活动痕迹，单独列入。静态资源与 /healthz 不审计：无安全语义的
#: 噪音。新增域必须同步此表，否则该域的调用（含 401/403）不留痕——test_audit.py
#: 有跨域覆盖钉住它。
_AUDITED_PREFIXES = ("/api/v1", "/ota/manifest")

_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$", re.IGNORECASE)

PRUNE_INTERVAL_S = 6 * 3600.0


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
    """判定调用方种类，不记录凭据明文。

    凭据明文进审计等于把密钥又抄了一份；这里只记种类与可关联的标识：
    会话记用户名（签名校验在 SessionMiddleware 已完成），Bearer 凭据记摘要
    前 12 位（足以对账、不足以冒用）。
    """
    from app.core.tokens import extract_bearer, hash_token

    username = request.session.get("u")
    if isinstance(username, str) and username:
        return "session", username
    token = extract_bearer(request.headers.get("authorization"))
    if token:
        return "token", hash_token(token)[:12]
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


def record_gateway(device_id: str, event: str, client_ip: str = "") -> None:
    """网关生命周期一行（connect/disconnect）。kind='gateway'，status 约定 0=连接 1=断开。"""
    from app.persistence import audit as audit_store

    status_code = 0 if event == "connect" else 1
    with session_scope() as session:
        audit_store.insert_api_call(
            session,
            kind=ApiCallKind.GATEWAY,
            method="WS",
            path="/gateway/device",
            status_code=status_code,
            duration_ms=0,
            device_id=device_id,
            actor_kind="device",
            actor=device_id,
            client_ip=client_ip or None,
        )


#: 各保留期表 → (时刻列, 保留天数)。天数与各表模型的 docstring 声明一致——
#: 声明=实现，两边由 tests/test_retention.py 钉住。业务数据（喷头状态迁移、
#: 使用会话）是运营历史，永久保留、不在此列。清单住在 app 根：它横跨各域的表，
#: 持久化层不得反向 import 各域模型（见 api_calls.prune）。
RETENTIONS: tuple[tuple[Any, Any, int], ...] = (
    (ApiCall, ApiCall.requested_at, 90),
    (CommandAudit, CommandAudit.created_at, 90),
    (JobRun, JobRun.occurred_at, 180),
    (DeviceLinkEvent, DeviceLinkEvent.occurred_at, 90),
    (DoorSnapshot, DoorSnapshot.received_at, 30),
    (VoiceDelivery, VoiceDelivery.occurred_at, 90),
)


async def prune_once() -> None:
    """清理一轮；生命周期（启动、取消、间隔）由 core/background 统一管理。"""
    with session_scope() as session:
        removed = api_calls.prune(session, settings.audit_retention_days, RETENTIONS)
    if removed:
        log_event(
            "INFO",
            "audit.pruned",
            "审计过期行已清理",
            rows=removed,
            retention_days=settings.audit_retention_days,
        )


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

    写库经线程池走 SQLAlchemy 会话（db.session 的 session_scope），不在事件循环里做同步 IO。
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
                api_calls.record,
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
