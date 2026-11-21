"""HTTP 错误响应：对齐 RFC 9457 Problem Details（application/problem+json）。

type 取自 core.problems 登记表，客户端据此分支不解析文案；title 是该类型的
稳定简述，detail 是本次请求的具体信息。未登记的问题（框架抛出的 404、405 等）
按状态码生成 type：这类问题没有可分支的语义。生产环境经 PUBLIC_BASE_URL 注入
正式域名前缀，未配置时输出相对引用；任何情况下不硬编码域名。
"""

import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .problems import VALIDATION_FAILED, ApiProblemError, Problem

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_PATH = "/problems/"


class ProblemDetail(BaseModel):
    """RFC 9457 problem+json 响应体：全部非 2xx 响应的统一形状。"""

    type: str = Field(description="问题类型 URI（/problems/<code>，可解析），客户端据此分支，不解析文案")
    title: str = Field(description="该问题类型的稳定简述")
    status: int = Field(description="HTTP 状态码")
    detail: str = Field(description="本次请求的具体说明")


#: 各状态码在契约里的通用描述；具体问题类型见 /problems/<code> 的可解析登记表。
_STATUS_DESCRIPTIONS: dict[int, str] = {
    401: "未认证：凭据缺失、无效或设备注册令牌无效",
    403: "权限不足：scope 不满足或设备不在密钥作用域",
    404: "目标资源不存在",
    409: "与设备或资源的当前状态冲突（离线、占用、端口冲突等）",
    413: "请求体超过体积上限",
    422: "请求内容校验失败",
    502: "设备侧执行失败（网关返回失败结果）",
}


def problem_responses(*statuses: int) -> "dict[int | str, dict[str, Any]]":
    """构建 OpenAPI responses 声明：错误响应一律 problem+json（RFC 9457）。

    FastAPI 对带校验参数的路由会自动生成 422 响应，形状是框架默认的
    HTTPValidationError——与实际返回的 problem+json 不符。各路由以本函数声明
    422 即覆盖它；其余状态码按各路由守卫与业务真正可能抛出的声明。用 "model"
    形式让 FastAPI 登记并引用 ProblemDetail 组件（契约里统一形状，单一出处）。
    """
    return {status: {"description": _STATUS_DESCRIPTIONS[status], "model": ProblemDetail} for status in statuses}


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

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        """未登记异常的最后防线：一律 500，并留完整栈。

        没有这一层时，未捕获异常走框架默认 500（{"detail": ...}），栈只在服务器
        标准输出里闪一次——排障时无从关联到请求。类型统一为 internal，栈进日志。
        """
        from .logging import log_event

        log_event(
            "ERROR",
            "http.unhandled",
            "请求处理出现未登记异常",
            error=repr(exc),
            path=_request.url.path,
            trace=traceback.format_exc(),
        )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


def _unregistered(status: int) -> Problem:
    """框架或第三方抛出的问题：按状态码生成 type，无可分支语义。

    文案不区分状态码：404 与 405 都是"请求本身打错了"，逐个状态码维护中文短语
    只是复刻 HTTP 标准表，状态码本身在 status 成员里。
    """
    code = f"http-{status}"
    return Problem(code, status, "请求失败")
