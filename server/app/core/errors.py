"""HTTP 错误响应：对齐 RFC 9457 Problem Details（application/problem+json）。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_CONTENT_TYPE = "application/problem+json"

# RFC 9457：type 是标识问题类型的 URI 引用（此处用稳定片段标识符）
_TYPE_BASE = "https://ntu-shower.example.com/problems/"


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"type": f"{_TYPE_BASE}{status}", "title": title, "status": status, "detail": detail},
        media_type=_CONTENT_TYPE,
    )


def install_error_handlers(app: FastAPI) -> None:
    """装配统一的 problem+json 错误响应（覆盖框架默认的 {"detail": ...}）。"""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _problem(exc.status_code, _title_for(exc.status_code), detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors())
        return _problem(422, "validation failed", detail)


def _title_for(status: int) -> str:
    from http import HTTPStatus

    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "error"
