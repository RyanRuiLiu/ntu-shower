"""HTTP 错误响应：对齐 RFC 9457 Problem Details（application/problem+json）。

## 两个成员的职责划分

- `type`：问题类型的 URI 引用，取自 `core.problems` 登记表（RFC 9457 §3.1 要求
  不同问题有不同 type）。客户端据此分支，不解析文案；
- `title`：该类型的稳定简述；`detail`：本次请求的具体信息，允许变化。

未登记的问题（框架抛的 404/405 等）退回按状态码生成 type——这类问题没有
可分支的语义，客户端本就不该依赖其文案。

生产环境经 PUBLIC_BASE_URL 注入正式域名前缀；未配置时输出相对引用，
相对引用按 RFC 3986 相对于请求 URI 解析。任何情况下都不硬编码域名
（AGENTS.md 禁止硬编码环境值）。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .problems import VALIDATION_FAILED, ApiProblemError, Problem

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_PATH = "/problems/"


def _problem(problem: Problem, detail: str) -> JSONResponse:
    base = settings.public_base_url.rstrip("/")
    return JSONResponse(
        status_code=problem.status,
        content={
            "type": f"{base}{PROBLEM_TYPE_PATH}{problem.code}",
            "title": problem.title,
            "status": problem.status,
            "detail": detail,
        },
        media_type=PROBLEM_MEDIA_TYPE,
        headers={"X-Problem-Type": problem.code},
    )


def install_error_handlers(app: FastAPI) -> None:
    """装配统一的 problem+json 错误响应（覆盖框架默认的 {"detail": ...}）。"""

    @app.exception_handler(ApiProblemError)
    async def api_problem(_request: Request, exc: ApiProblemError) -> JSONResponse:
        return _problem(exc.problem, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "request failed"
        return _problem(_unregistered(exc.status_code), detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception(_request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(str(loc) for loc in error['loc'])}: {error['msg']}" for error in exc.errors())
        return _problem(VALIDATION_FAILED, detail)


def _unregistered(status: int) -> Problem:
    """框架/第三方抛出的问题：按状态码生成 type，无可分支语义。

    文案不区分状态码——这类问题（404 路径不存在、405 方法不对）都是"请求本身
    打错了"，对使用者没有可操作的区别，而逐个状态码维护中文短语只是复刻
    HTTP 标准表。具体状态码在 `status` 成员里，需要时看得到。
    """
    code = f"http-{status}"
    return Problem(code, status, "请求失败")
