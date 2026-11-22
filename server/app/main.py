"""ntu-shower 服务端入口：装配设备网关平面与各领域模块。

## 接口组织（版本化的统一资源树）

对外 HTTP 面统一挂 `/api/v1` 前缀（路径版本化，对外契约的破坏性变更升 v2）；
URL 只表达资源，不表达权限——谁能调用由 scope 守卫决定（RFC 6749 scope 语义，
标准 Bearer 凭据或会话 Cookie，见 auth/dependencies）：

- /api/v1/devices、/api/v1/doors     设备域：精简清单公开；runtime 档案与远程
                                      操作 devices:write；分配/结算/拍照按
                                      sessions:write / devices:photo
- /api/v1/config                     配置域（config:read / config:write）
- /api/v1/users                      用户域：名单与登记/编辑/撤销、密钥签发/吊销
                                      （users:write）；`/users/me` 是自己的记录
- /api/v1/ota、/ota/manifest         发布域：清单与下载公开，发布/下架/重推 ota:write；
                                      manifest 为设备硬编码契约（无前缀，路径冻结）
- /api/v1/audit                      审计域（audit:read）
- /api/v1/stream                     实时推送（公开，可按 device_id 过滤）
- /gateway/device                    设备 WS 网关（设备硬编码契约，路径冻结）
- /voice/assets/{id}.mp3             语音素材下载（设备硬编码契约，路径冻结）
- /problems/{code}                   RFC 9457 问题类型登记表（type URI 的可解析目标）

模块按领域划分（modules/<域>），URL 按资源组织：/devices/* 由 devices、
sessions、bridge、config 各域按资源归属提供端点。网页端（web/）挂在根路径，
API 路由先注册，不会被静态文件抢走。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from typing_extensions import TypedDict

from . import audit_middleware, retention
from .auth.routes import admin_router as users_admin_router
from .auth.routes import identity_router as users_identity_router
from .auth.routes import router as auth_router
from .core.background import Background
from .core.config import settings
from .core.connections import manager
from .core.errors import install_error_handlers, problem_responses
from .core.logging import configure_logging, log_event
from .core.problems import REGISTRY
from .core.version import APP_VERSION
from .db.base import utcnow
from .db.session import Database
from .gateway.routes import router as gateway_router
from .modules.audit.routes import admin_router as audit_admin_router
from .modules.bridge.routes import admin_router as bridge_admin_router
from .modules.bridge.routes import router as bridge_router
from .modules.config import notifier as config_notifier
from .modules.config.routes import admin_router as config_admin_router
from .modules.devices import push as devices_push
from .modules.devices.routes import admin_router as devices_admin_router
from .modules.devices.routes import device_read_router as devices_device_read_router
from .modules.devices.routes import device_router as devices_device_router
from .modules.devices.routes import router as devices_router
from .modules.doors.routes import router as doors_router
from .modules.freelist.routes import router as freelist_router
from .modules.jobs.routes import admin_router as jobs_admin_router
from .modules.ota.routes import admin_router as ota_admin_router
from .modules.ota.routes import device_router as ota_device_router
from .modules.ota.routes import router as ota_router
from .modules.power import service as power_service
from .modules.power.routes import router as power_router
from .modules.sessions import scheduler as sessions_scheduler
from .modules.sessions.routes import router as sessions_router
from .modules.status_line import service as status_line_service
from .modules.voice.routes import admin_router as voice_admin_router
from .modules.voice.routes import router as voice_router
from .modules.water.routes import router as water_router
from .persistence import audit as command_audit_store

#: 要求浏览器每次回来验证的前端资源后缀。
STATIC_SUFFIXES = (".html", ".js", ".css")

#: 管理台会话 Cookie 名。不取库默认的通用名 "session"：同一域名的其他应用若也用
#: 通用名（路径同为 /），会互相覆盖对方的 Cookie。
SESSION_COOKIE_NAME = "ntu_session"


def _cookie_secure() -> bool:
    """配了 https 对外基址才置 Secure；本地与测试走 http，Cookie 仍要能用。"""
    return settings.public_base_url.startswith("https://")


#: SESSION_SECRET 缺失时的启动错误——错误消息只在这里出现一次。
ERR_SESSION_SECRET = (
    "SESSION_SECRET 未配置：会话签名密钥不能为空。"
    '在 .env / 环境变量里注入一枚随机值（如 `python -c "import secrets; print(secrets.token_urlsafe(32))"`）。'
)


class HealthStatus(TypedDict):
    """存活探针的应答；version 让运维能一眼确认线上跑的是哪一版。"""

    status: str
    name: str
    version: str


#: 主动断开设备时使用的 WS 关闭码（RFC 6455 §7.4.1：1001 = Going Away，端点
#: 主动离开的标准码）。不能用 uvicorn 默认关闭的 1012——那是基础设施保留码，
#: OkHttp 会以 ProtocolException 拒收。
WS_CLOSE_GOING_AWAY = 1001


async def _close_device_connections() -> None:
    """lifespan 关闭时向全部在线设备发 1001 关闭帧，抢在 uvicorn 的 1012 之前。

    不在这里做收尾（记 link event、刷 last_seen）——那些由各连接自己的 finally
    路径完成，这里只负责让设备收到一个协议上正确的关闭码。
    """
    for _device_id, ws in manager.online_connections():
        with suppress(Exception):
            await ws.close(code=WS_CLOSE_GOING_AWAY)


class ProblemDoc(BaseModel):
    """问题类型的可解析说明（RFC 9457 type URI 的目标）。"""

    code: str = Field(description="问题类型码（kebab-case，全局唯一）")
    title: str = Field(description="稳定简述，与问题响应体的 title 一致")
    status: int = Field(description="该类型默认的 HTTP 状态码")


def _abort_pending_commands() -> None:
    """启动时收尾上一进程留下的 pending 命令（审计页不再永久显示进行中）。

    在 lifespan 里调而不是 init_schema：新表的建立归 Alembic（compose 的 migrate
    服务先于应用启动），init_schema 的旧建表阶段它们还不存在。
    """
    from .db.session import session_scope

    with session_scope() as session:
        aborted = command_audit_store.abort_pending(session, utcnow())
    if aborted:
        log_event("WARNING", "command.aborted-on-startup", "启动时收尾未完成命令", count=aborted)


def create_app() -> FastAPI:
    # 先装配日志：此后的初始化失败才有痕迹可查
    configure_logging(settings.log_level)
    Path(settings.ota_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.voice_dir).mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 数据库是全应用唯一的会话来源；表结构归 Alembic（compose 的 migrate
        # 服务先于应用启动）。探活一次：库打不开/不可写在这里就炸，不拖到首个请求。
        _app.state.database = Database(settings.effective_database_url)
        with _app.state.database.new_session() as session:
            session.execute(text("SELECT 1"))
        # 间隔任务统一装配：各模块只提供「一轮做什么」，生命周期集中在这一个对象上。
        background.interval("power", power_service.TICK_INTERVAL_S, power_service.tick_once)
        background.interval("status-line", status_line_service.PUSH_INTERVAL_S, status_line_service.tick)
        background.interval("audit-prune", retention.PRUNE_INTERVAL_S, retention.prune_once)
        # 库维护：设备夜间关机后的静默窗口里归还滚动删除留下的空闲页（VACUUM）
        background.interval("maintenance", retention.VACUUM_INTERVAL_S, retention.maybe_vacuum)
        _abort_pending_commands()
        power_service.start()
        background.start()
        # 事件驱动型任务保持自己的模型：按设备惰性起 worker，不占统一循环。
        sessions_scheduler.start()
        yield
        # 先断设备 WS 再拆底层：uvicorn 关闭时对每条 WS 发 close code 1012
        # （Service Restart），但 RFC 6455 §7.4.1 明确 1012 保留给基础设施、
        # 应用不得发送——OkHttp 正确抛 ProtocolException 拒收，设备看到的是
        # 协议错误而不是正常的部署断开。这里抢先发 1001（Going Away，端点
        # 主动离开的标准码），设备收到 onClosed(1001) 走正常重连。
        await _close_device_connections()
        await sessions_scheduler.stop()
        await background.stop()
        await power_service.stop()
        _app.state.database.dispose()

    # 调试 UI（/docs、/redoc）不对外：对外契约是 /openapi.json（外部调用方拿它
    # 生成客户端），调试面没有消费方，暴露它只会泄露接口拓扑（OWASP API8）。
    # info.description 是外部调用方的唯一入口说明：认证方式、密钥签发渠道、
    # 错误形状——openapi.json 的其余部分只描述端点本身，回答不了"拿什么调"。
    app = FastAPI(
        title="NTU Shower",
        version=APP_VERSION,
        description=(
            "浴室中控系统的对外 API。\n\n"
            "## 认证\n\n"
            "除标注公开的端点（设备清单、/doors、/devices/enroll、/healthz）外，"
            "全部端点要求 `Authorization: Bearer <密钥>` 头。\n\n"
            "密钥（`ntushower_` 前缀）由管理员在管理台「用户与密钥」页签发，"
            "权限集（scopes）与设备作用域附于每枚密钥，永久有效直至吊销；"
            "同一用户可持有多个密钥以便轮换。各端点要求的 scope 见 securitySchemes。\n\n"
            "## 错误\n\n"
            "非 2xx 响应一律 RFC 9457 problem+json：`type`（可分支的错误类别 URI）、"
            "`title`（稳定简述）、`detail`（本次的具体中文说明）。\n\n"
            "## WebSocket\n\n"
            "OpenAPI 不描述 WebSocket 端点，本节即两条 WebSocket 通道的全部契约；"
            "两者均经 WSS 接入（与 HTTPS 同端口、同证书）。\n\n"
            "- 推送通道 `wss://<host>/api/v1/stream`：面向网页端与外部订阅方，公开，"
            "无需认证。查询参数 `device_id`（可选）：指定后仅推送该设备的事件。\n"
            "  连接为服务端单向推送：客户端不得发送任何帧，服务端将任何来帧视为断开"
            "信号；链路保活由传输层 ping/pong 承担。帧为 JSON 文本，形状统一为"
            ' `{"type": "<事件类型>", "payload": {...}}`：\n'
            "  - `door.update`——喷头现态变化；payload 为该设备的完整现态"
            "（与 `GET /doors` 单台结果同构）；\n"
            "  - `device.update`——设备在场或档案变化；payload 为精简档案"
            "（device_id、name、online、retired）加 `at` 时间戳。\n"
            "  推送为尽力而为：服务端队列满时丢弃事件，断线窗口内的状态不随事件补发。"
            "恢复职责在客户端——重连后先调用 `GET /doors` 取全量现态，此后以推送为准。\n"
            "- 设备通道 `wss://<host>/gateway/device`：设备端拨入专用，路径冻结；"
            "帧结构带帧级协议版本，JSON Schema 见仓库 `protocol/schema/*.json`。"
        ),
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    install_error_handlers(app)
    audit_middleware.install_audit(app)
    # 配置保存 → 设备推送 + 定时器重算：两个订阅者各司一职，组合根装配（写侧
    # 不 import 任何订阅方，见 modules/config/notifier.py 的模块注释）。
    config_notifier.register(devices_push.on_config_change)
    config_notifier.register(sessions_scheduler.on_config_change)

    app.include_router(gateway_router)
    app.include_router(doors_router)
    app.include_router(sessions_router)
    app.include_router(devices_router)
    app.include_router(devices_admin_router)
    app.include_router(devices_device_router)
    app.include_router(devices_device_read_router)
    app.include_router(freelist_router)
    app.include_router(power_router)
    app.include_router(config_admin_router)
    app.include_router(ota_router)
    app.include_router(ota_admin_router)
    app.include_router(ota_device_router)
    app.include_router(bridge_router)
    app.include_router(bridge_admin_router)
    app.include_router(audit_admin_router)
    # users_identity_router 必须排在 users_admin_router 之前：后者要求 users:write，
    # 将来它的 `/users/{username}` 若加上 GET，会把这个 `/users/me` 抢走。
    app.include_router(users_identity_router)
    app.include_router(users_admin_router)
    app.include_router(jobs_admin_router)
    app.include_router(voice_router)
    app.include_router(water_router)
    app.include_router(voice_admin_router)
    app.include_router(auth_router)

    @app.get("/healthz", tags=["meta"], summary="存活探针")
    def health() -> HealthStatus:
        return {"status": "ok", "name": "ntu-shower", "version": APP_VERSION}

    @app.get(
        "/problems/{problem_code}",
        tags=["meta"],
        summary="问题类型说明",
        response_model=ProblemDoc,
        responses=problem_responses(404, 422),
    )
    def problem_doc(problem_code: str) -> ProblemDoc:
        """RFC 9457 type URI 的可解析目标：返回问题类型的稳定登记信息。

        问题响应体的 `type` 成员（/problems/<code>）指到这里；未登记的 code 按未
        找到的资源处理（404 problem+json）。
        """
        for problem in REGISTRY:
            if problem.code == problem_code:
                return ProblemDoc(code=problem.code, title=problem.title, status=problem.status)
        raise StarletteHTTPException(status_code=404)

    # 网页端挂在根路径：零构建单页（index.html + views/ 视图切换，无前端路由），
    # StaticFiles html=True 直接托管源码树。挂在全部 API 路由之后，未匹配路径才
    # 落到这里；StaticFiles 对不存在的文件抛 404，经统一错误面回 problem+json——
    # 接口错误与未知路径都不伪装成 200 的 HTML。
    web_dir = Path(__file__).resolve().parent.parent / "web"

    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    # 前端资源要求浏览器每次都回来验一遍：no-cache 不是"不缓存"，而是带 ETag
    # 协商，没变 304，变了才重新下载。实时状态 API 则一律 no-store：不带任何缓存
    # 头时客户端（微信 iOS 的 wx.request 尤其激进）会启发式缓存 GET，下拉刷新
    # 拿到的是旧数据而 WS 推送才是新的——两份数据源打架，表现就是"刷新了但数据不动"。
    @app.middleware("http")
    async def revalidate_web_assets(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        if request.url.path.endswith(STATIC_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # 会话走 Starlette SessionMiddleware（签名基于 itsdangerous）：签发、校验、过期与
    # 注销都是库的职责，端点只读写 request.session。必须最后注册——add_middleware
    # 越晚加越靠外，审计中间件要读到 request.session 就得在它内侧。
    if not settings.session_secret:
        raise RuntimeError(ERR_SESSION_SECRET)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=SESSION_COOKIE_NAME,
        max_age=settings.session_idle_seconds,
        same_site="lax",
        https_only=_cookie_secure(),
    )

    return app


background = Background()

app = create_app()
