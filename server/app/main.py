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

模块按领域划分（modules/<域>），URL 按资源组织：/devices/* 由 devices、
sessions、bridge、config 各域按资源归属提供端点。网页端（web/）挂在根路径，
API 路由先注册，不会被静态文件抢走。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from typing_extensions import TypedDict

from . import audit_middleware
from .auth.routes import admin_router as users_admin_router
from .auth.routes import identity_router as users_identity_router
from .auth.routes import router as auth_router
from .core.background import Background
from .core.config import settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, log_event
from .core.version import APP_VERSION
from .db.base import utcnow
from .db.session import Database
from .gateway.routes import router as gateway_router
from .modules.audit.routes import admin_router as audit_admin_router
from .modules.bridge.routes import admin_router as bridge_admin_router
from .modules.bridge.routes import router as bridge_router
from .modules.config import notifier as config_notifier
from .modules.config.routes import admin_router as config_admin_router
from .modules.devices.routes import admin_router as devices_admin_router
from .modules.devices.routes import device_read_router as devices_device_read_router
from .modules.devices.routes import device_router as devices_device_router
from .modules.devices.routes import router as devices_router
from .modules.doors.routes import router as doors_router
from .modules.extend_hours import service as extend_hours_service
from .modules.freelist.routes import router as freelist_router
from .modules.jobs.routes import admin_router as jobs_admin_router
from .modules.ota.routes import device_router as ota_device_router
from .modules.ota.routes import router as ota_router
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
        background.interval("extend-hours", extend_hours_service.TICK_INTERVAL_S, extend_hours_service.tick_once)
        background.interval("status-line", status_line_service.PUSH_INTERVAL_S, status_line_service.tick)
        background.interval("audit-prune", audit_middleware.PRUNE_INTERVAL_S, audit_middleware.prune_once)
        _abort_pending_commands()
        background.start()
        # 事件驱动型任务保持自己的模型：按设备惰性起 worker，不占统一循环。
        sessions_scheduler.start()
        yield
        await sessions_scheduler.stop()
        await background.stop()
        _app.state.database.dispose()

    # 调试 UI（/docs、/redoc）不对外：对外契约是 /openapi.json（外部调用方拿它
    # 生成客户端），调试面没有消费方，暴露它只是白送接口拓扑（OWASP API8）。
    app = FastAPI(title="NTU Shower", version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)
    install_error_handlers(app)
    audit_middleware.install_audit(app)
    # 配置保存 → 定时器重算 + 设备推送：组合根装配（写侧不 import sessions，见
    # modules/config/notifier.py 的模块注释）。
    config_notifier.register(sessions_scheduler.on_config_change)

    app.include_router(gateway_router)
    app.include_router(doors_router)
    app.include_router(sessions_router)
    app.include_router(devices_router)
    app.include_router(devices_admin_router)
    app.include_router(devices_device_router)
    app.include_router(devices_device_read_router)
    app.include_router(freelist_router)
    app.include_router(config_admin_router)
    app.include_router(ota_router)
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

    @app.get("/healthz", tags=["meta"])
    def health() -> HealthStatus:
        return {"status": "ok", "name": "ntu-shower", "version": APP_VERSION}

    # 网页端挂在根路径：它就是这个服务唯一的人机界面，而 API 路由已在前注册。
    # history 路由的 SPA fallback 用 catch-all 路由（FastAPI 社区主流写法）：
    # - API/网关/健康检查前缀的未匹配路径回 404 problem+json——接口错误绝不能
    #   伪装成 200 的 HTML；
    # - 其余未匹配路径：dist 里真实存在的文件（assets、favicon）按文件回，
    #   否则回 dist/index.html，让 /devices 等前端路由刷新时落到应用入口。
    # 本地开发时 dist 不存在则回退 web/ 根的源 index.html（Vite dev server 供 JS）。
    # legacy 前缀例外：它是与新版并存的旧版界面（同一套新契约、不同界面实现），
    # 源码树是 server/web-legacy/（零构建，服务端直接托管），不回 SPA 入口。
    server_dir = Path(__file__).resolve().parent.parent
    dist_dir = server_dir / "web" / "dist"
    legacy_dir = server_dir / "web-legacy"

    app.mount("/legacy", StaticFiles(directory=str(legacy_dir), html=True), name="legacy")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa_fallback(spa_path: str) -> Response:
        if spa_path.startswith(("api/", "gateway/", "ota/", "healthz", "problems/", "legacy/")):
            raise StarletteHTTPException(status_code=404)
        base = dist_dir if dist_dir.is_dir() else server_dir / "web"
        candidate = base / spa_path
        if "." in Path(spa_path).name and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(base / "index.html")

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
