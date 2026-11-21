"""HTTP 审计的采集与装配：谁、何时、对什么、结果如何，一行一个调用。

按 OWASP Logging Cheat Sheet 的事件清单与 NIST SP 800-92 的保留框架设计，
标准依据见 docs/adr/0014-observability.md。覆盖 /api/v1 与 /ota/manifest 的全部
请求（认证、授权、参数与服务端错误天然落表）；静态资源与 /healthz 不审计。
设备网关的连接与断开事实的唯一出处是 devices 的 device_link_events，本表不重复
记录（同一事实一处）。

体原文全有或全无、不截断、不脱敏（2026-11-22 业主裁定维持 2026-10-01 的「如实
记录」：审计的价值在于如实，这是对 OWASP 禁记清单的显式偏离，ADR-0014 记录在案；
stdout 日志不写这些字段）。数据重型且可重取的截图、拍照、UI 节点树与 multipart
上传体除外。过期行的清理与各表保留期登记在 app/retention.py（横切运维，不属
HTTP 采集）。

存储在 persistence/api_calls.py：采集归 HTTP 侧，落库归持久化层——core 保持
零向上依赖。

## 纯 ASGI 中间件

Starlette 官方文档对 BaseHTTPMiddleware 明示了限制（contextvar 不向上传播、
WebSocket 不支持、流式与内存问题），推荐直接实现 ASGI 中间件——本装配采用该
形态：请求体经 receive 缓冲后回放给下游，响应体经包装 send 旁路累积、消息原样
透传（不重建响应对象）。WebSocket（scope type 非 http）原样放行。
"""

import contextlib
import json
import re
import time
import uuid

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var, trace_id_var
from app.persistence import api_calls

#: 需要审计的路径前缀 = 版本化对外面的全部域（/api/v1，见 main.py 的模块注释）。
#: /ota/manifest 是设备硬编码契约，无版本前缀（见 ota/routes.py），但它是设备
#: 最频繁的一手活动痕迹，单独列入。静态资源与 /healthz 不审计：无安全语义的
#: 噪音。新增域必须同步此表，否则该域的调用（含 401/403）不留痕——test_audit.py
#: 有跨域覆盖钉住它。
_AUDITED_PREFIXES = ("/api/v1", "/ota/manifest")

#: W3C Trace Context 1.0 的 traceparent 形态（官方 ABNF）：version 2 位小写十六进制
#: （`ff` 禁止）、trace-id 32 位小写十六进制（全零禁止）、parent-id 16 位小写十六
#: 进制（全零禁止）、flags 2 位小写十六进制。不合法的 traceparent 一律忽略，按
#: 规范由本端生成 trace-id。
_TRACEPARENT = re.compile(r"^(?!ff-)(?!00-0{32}-)[0-9a-f]{2}-([0-9a-f]{32})-(?!0{16}-)[0-9a-f]{16}-[0-9a-f]{2}$")

#: 请求体与响应体入库的体积上限。全有或全无，绝不截断：截断了后续就查不回真实
#: 内容，超限的记录占位说明（媒体类响应可重取，与不落媒体的取舍一致）。普通
#: JSON 请求与响应都在 KB 量级，全文入库。
BODY_MAX_BYTES = 256 * 1024

#: 响应体不落库的路径：数据重型且可重取的截图、拍照、UI 节点树三个端点。
_DATA_HEAVY_PATH = re.compile(r"/(capture|ui-dump|photo)$")


def resolve_trace_id(traceparent: str | None) -> str:
    """取 W3C trace-id：调用方带合法 `traceparent` 就沿用它的 trace-id，否则生成。

    只取 trace-id 一段：span-id 是调用方那次调用的，与"服务端这一行审计"不是一回事。
    """
    if traceparent:
        match = _TRACEPARENT.match(traceparent.strip())
        if match:
            return match.group(1)
    return uuid.uuid4().hex


def _scope_header(scope: Scope, name: str) -> str:
    """读 scope 头部的第一个值；不存在给空串。头名按 HTTP 语义不区分大小写。"""
    key = name.lower().encode("latin-1")
    for field, value in scope.get("headers") or ():
        if field == key:
            return str(value.decode("latin-1"))
    return ""


def _actor_of(scope: Scope) -> tuple[str, str]:
    """判定调用方种类，不记录凭据明文。

    凭据明文进审计等于把密钥又抄了一份；这里只记种类与可关联的标识：
    会话记用户名（签名校验在 SessionMiddleware 已完成），Bearer 凭据记摘要
    前 12 位（足以对账、不足以冒用）。
    """
    from app.core.tokens import extract_bearer, hash_token

    username = (scope.get("session") or {}).get("u")
    if isinstance(username, str) and username:
        return "session", username
    token = extract_bearer(_scope_header(scope, "authorization"))
    if token:
        return "token", hash_token(token)[:12]
    return "anonymous", ""


def store_body(body: str) -> str:
    """体积在限内原文返回，超限返回占位说明，绝不截断。"""
    if len(body.encode()) <= BODY_MAX_BYTES:
        return body
    return f"[JSON 体 {len(body.encode())} 字节，超出 {BODY_MAX_BYTES} 字节上限未存储]"


class AuditMiddleware:
    """一行一个 API 调用，并回填 X-Request-ID 响应头。

    写库经线程池走 SQLAlchemy 会话（db.session 的 session_scope），不在事件循环里
    做同步 IO。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(_AUDITED_PREFIXES):
            await self.app(scope, receive, send)
            return
        await self._audit(scope, receive, send)

    async def _audit(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        method = scope["method"]
        request_id = uuid.uuid4().hex
        trace_id = resolve_trace_id(_scope_header(scope, "traceparent"))
        request_id_var.set(request_id)
        trace_id_var.set(trace_id)

        # 请求体原文落库。只对 JSON 体做：multipart（APK 上传）本体已存
        # ota_releases，审计行记版本与大小即完整事实。按 "json" 判定而非全等：
        # application/problem+json 也含 json，而与 application/json 全等反而
        # 匹配不上它。读完缓冲回放，下游照常解析。
        request_body = ""
        if method in ("POST", "PUT", "PATCH", "DELETE") and "json" in _scope_header(scope, "content-type"):
            body_bytes = await self._drain(receive)

            async def replay_receive() -> Message:
                return {"type": "http.request", "body": body_bytes, "more_body": False}

            receive = replay_receive
            request_body = store_body(body_bytes.decode(errors="replace"))

        started = time.perf_counter()
        captured = _ResponseCapture(path)

        async def send_wrapper(message: Message) -> None:
            await send(captured.observe(message, request_id))

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._record(scope, path, method, request_body, captured, duration_ms)

    async def _drain(self, receive: Receive) -> bytes:
        """读干请求体消息流；断连时返回已收到的部分。"""
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body") or b""
                if not message.get("more_body"):
                    return body
            elif message["type"] == "http.disconnect":
                return body

    async def _record(
        self,
        scope: Scope,
        path: str,
        method: str,
        request_body: str,
        captured: "_ResponseCapture",
        duration_ms: int,
    ) -> None:
        query = (scope.get("query_string") or b"").decode("latin-1")
        # 来源 IP：经 nginx 代理而来，真实地址在 X-Real-IP 里
        client_ip = _scope_header(scope, "x-real-ip")
        if not client_ip:
            client = scope.get("client")
            client_ip = client[0] if client else ""
        actor_kind, actor = _actor_of(scope)
        await run_in_threadpool(
            api_calls.record,
            method=method,
            path=path,
            query=query,
            status_code=captured.status_code,
            duration_ms=duration_ms,
            actor_kind=actor_kind,
            actor=actor,
            client_ip=client_ip,
            user_agent=_scope_header(scope, "user-agent"),
            request_body=request_body,
            response_body=captured.stored_body(),
            problem_type=captured.problem_type,
            detail=captured.detail,
        )


class _ResponseCapture:
    """响应消息的旁路观察者：状态码、响应体与 problem 明细。

    content-type 判定在 response.start 到达后生效——媒体类响应（截图、拍照的
    base64）不累积，数据重型路径（_DATA_HEAVY_PATH）不累积；其余 JSON 响应的
    体量在 KB 量级，累积副本在审计写库时消费。消息原样透传，不重建响应。
    problem 的 type 与 detail 取自 RFC 9457 响应体官方字段（type/detail）。
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._chunks: list[bytes] = []
        self._capture = False
        self._problem = False
        self.status_code = 0
        self._problem_body = b""

    def observe(self, message: Message, request_id: str) -> Message:
        """观察一条响应消息并原样返回（response.start 会追加 X-Request-ID）。"""
        if message["type"] == "http.response.start":
            self.status_code = int(message.get("status") or 0)
            content_type = _message_header(message, "content-type")
            self._capture = "json" in content_type and not _DATA_HEAVY_PATH.search(self._path)
            self._problem = "problem+json" in content_type
            headers = list(message.get("headers") or ())
            headers.append((b"x-request-id", request_id.encode("latin-1")))
            message = {**message, "headers": headers}
        elif message["type"] == "http.response.body":
            if self._capture:
                self._chunks.append(message.get("body") or b"")
            if self._problem:
                self._problem_body += message.get("body") or b""
        return message

    def stored_body(self) -> str:
        """审计行的响应体字段：不落库的路径给空串，其余按体积上限原文化。"""
        if not self._chunks:
            return ""
        return store_body(b"".join(self._chunks).decode(errors="replace"))

    @property
    def problem_type(self) -> str:
        """problem 响应的 type：RFC 9457 官方字段，非 problem 响应给空串。"""
        value = self._problem_field("type")
        return value.rsplit("/", 1)[-1] if value else ""

    @property
    def detail(self) -> str:
        """problem 响应的 detail：失败说明的中文原文，供审计行直接展示。"""
        return self._problem_field("detail")

    def _problem_field(self, field: str) -> str:
        if not self._problem_body:
            return ""
        with contextlib.suppress(ValueError):
            parsed = json.loads(self._problem_body)
            if isinstance(parsed, dict):
                value = parsed.get(field)
                if isinstance(value, str):
                    return value
        return ""


def _message_header(message: Message, name: str) -> str:
    """读响应起始消息头部的第一个值；不存在给空串。"""
    key = name.lower().encode("latin-1")
    for field, value in message.get("headers") or ():
        if field == key:
            return str(value.decode("latin-1"))
    return ""


def install_audit(app: FastAPI) -> None:
    """装配审计中间件。"""
    app.add_middleware(AuditMiddleware)
