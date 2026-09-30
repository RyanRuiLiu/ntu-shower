"""HTTP 错误响应：对齐 RFC 9457 Problem Details（application/problem+json）。

type 成员是标识"问题类型"的 URI 引用（RFC 9457 §3.1）。
生产环境经 PUBLIC_BASE_URL 注入正式域名前缀；未配置时输出相对引用，
相对引用按 RFC 3986 相对于请求 URI 解析。任何情况下都不硬编码域名
（docs/STANDARDS.md §7 禁止硬编码环境值）。
"""

from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_PATH = "/problems/"


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    base = settings.public_base_url.rstrip("/")
    return JSONResponse(
        status_code=status,
        content={
            "type": f"{base}{PROBLEM_TYPE_PATH}{status}",
            "title": title,
            "status": status,
            "detail": detail,
        },
        media_type=PROBLEM_MEDIA_TYPE,
    )


def install_error_handlers(app: FastAPI) -> None:
    """装配统一的 problem+json 错误响应（覆盖框架默认的 {"detail": ...}）。"""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _problem(exc.status_code, _title_for(exc.status_code), detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(_request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}" for error in exc.errors())
        return _problem(422, "validation failed", detail)


def _title_for(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "error"
