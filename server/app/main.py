"""ntu-shower 服务端入口：装配设备网关平面与各领域模块。

## 接口组织（版本化的统一资源树）

对外 HTTP 面统一挂 `/api/v1` 前缀（路径版本化，对外契约的破坏性变更升 v2）；
URL 只表达资源，不表达权限——谁能调用由能力位守卫决定（统一身份制，
标准 Bearer 凭据）：

- /api/v1/devices、/api/v1/doors     设备域：精简清单公开；runtime 档案与远程
                                      操作 admin；分配/结算/拍照按 session/photo 能力位
- /api/v1/config                     配置域（admin）
- /api/v1/identities                 身份域：名单与签发/编辑/撤销 admin；`/identities/me`
                                      是自己的记录（凭 Bearer 凭据）
- /api/v1/tasks                      后台任务登记：按 tasks 能力位增删某个学号的登记
- /api/v1/stats                      浴室统计：按 stats 能力位读一台设备的聚合、某一天的
                                     状态序列（客户端本地重放）、个人的跨设备统计
- /api/v1/ota、/ota/manifest         发布域：清单与下载公开，发布/下架/重推 admin；
                                      manifest 为设备硬编码契约（无前缀，路径冻结）
- /api/v1/audit                      审计域（admin）
- /api/v1/stream                     实时推送（公开，可按 device_id 过滤）
- /gateway/device                    设备 WS 网关（设备硬编码契约，路径冻结）

模块按领域划分（modules/<域>），URL 按资源组织：/devices/* 由 devices、
sessions、bridge、config 各域按资源归属提供端点。网页端（web/）挂在根路径，
API 路由先注册，不会被静态文件抢走。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from typing_extensions import TypedDict

from .auth.routes import router as auth_router
from .core import audit, db
from .core.background import Background
from .core.config import settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging, log_event
from .core.version import APP_VERSION
from .db.base import utcnow
from .db.session import Database
from .modules.access.routes import admin_router as identities_admin_router
from .modules.access.routes import router as identity_router
from .modules.audit import repository as command_audit_repository
from .modules.audit.routes import admin_router as audit_admin_router
from .modules.bridge.routes import admin_router as bridge_admin_router
from .modules.bridge.routes import router as bridge_router
from .modules.config import store as config_store
from .modules.config.routes import admin_router as config_admin_router
from .modules.devices import repository as devices_repository
from .modules.devices.routes import admin_router as devices_admin_router
from .modules.devices.routes import router as devices_router
from .modules.doors import repository as doors_repository
from .modules.doors.routes import router as doors_router
from .modules.extend_hours import repository as extend_hours_repository
from .modules.extend_hours import service as extend_hours_service
from .modules.freelist.routes import router as freelist_router
from .modules.gateway.routes import router as gateway_router
from .modules.ota import repository as ota_repository
from .modules.ota.routes import device_router as ota_device_router
from .modules.ota.routes import router as ota_router
from .modules.sessions import scheduler as sessions_scheduler
from .modules.sessions.routes import router as sessions_router
from .modules.stats.routes import router as stats_router
from .modules.status_line import service as status_line_service
from .modules.tasks.routes import admin_router as tasks_admin_router
from .modules.tasks.routes import router as tasks_router
from .modules.voice import repository as voice_repository
from .modules.voice.routes import admin_router as voice_admin_router
from .modules.voice.routes import router as voice_router
from .modules.water.routes import router as water_router
from .persistence import audit as command_audit_store

#: 要求浏览器每次回来验证的前端资源后缀。
STATIC_SUFFIXES = (".html", ".js", ".css")


class HealthStatus(TypedDict):
    """存活探针的应答；version 让运维能一眼确认线上跑的是哪一版。"""

    status: str
    name: str
    version: str


def init_schema() -> None:
    """建全部表。

    各领域仓储持有自己的表结构，这里是唯一的装配点：应用启动与测试夹具都调它。
    全部用 CREATE TABLE IF NOT EXISTS，重复调用没有副作用。
    """
    db.init_db()
    devices_repository.init_tables()
    doors_repository.init_tables()
    ota_repository.init_tables()
    config_store.init_tables()
    audit.init_tables()
    command_audit_repository.init_tables()
    extend_hours_repository.init_tables()
    voice_repository.init_tables()


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
    init_schema()
    Path(settings.ota_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.voice_dir).mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 新数据层的连接在这里建立：迁移尚未完成的模块仍走 core.db，
        # 两者指向同一个 SQLite 文件，互不干扰。
        _app.state.database = Database(settings.effective_database_url)
        # 间隔任务统一装配：各模块只提供「一轮做什么」，生命周期集中在这一个对象上。
        background.interval("extend-hours", extend_hours_service.TICK_INTERVAL_S, extend_hours_service.tick_once)
        background.interval("status-line", status_line_service.PUSH_INTERVAL_S, status_line_service.tick)
        background.interval("audit-prune", audit.PRUNE_INTERVAL_S, audit.prune_once)
        _abort_pending_commands()
        background.start()
        # 事件驱动型任务保持自己的模型：按设备惰性起 worker，不占统一循环。
        sessions_scheduler.start()
        yield
        sessions_scheduler.stop()
        await background.stop()
        _app.state.database.dispose()

    app = FastAPI(title="NTU Shower", version=APP_VERSION, lifespan=lifespan)
    install_error_handlers(app)
    audit.install_audit(app)

    app.include_router(gateway_router)
    app.include_router(doors_router)
    app.include_router(sessions_router)
    app.include_router(devices_router)
    app.include_router(devices_admin_router)
    app.include_router(freelist_router)
    app.include_router(config_admin_router)
    app.include_router(ota_router)
    app.include_router(ota_device_router)
    app.include_router(bridge_router)
    app.include_router(bridge_admin_router)
    app.include_router(audit_admin_router)
    # identity_router 必须排在 identities_admin_router 之前：后者挂着 `require_admin`，
    # 将来它的 `/identities/{identity}` 若加上 GET，会把这个 `/identities/me` 抢走。
    app.include_router(identity_router)
    app.include_router(identities_admin_router)
    app.include_router(tasks_router)
    app.include_router(tasks_admin_router)
    app.include_router(stats_router)
    app.include_router(voice_router)
    app.include_router(water_router)
    app.include_router(voice_admin_router)
    app.include_router(auth_router)

    @app.get("/healthz", tags=["meta"])
    def health() -> HealthStatus:
        return {"status": "ok", "name": "ntu-shower", "version": APP_VERSION}

    # 网页端挂在根路径：它就是这个服务唯一的人机界面，而 API 路由已在前注册。
    # 必须放在最后注册：Starlette 按注册顺序匹配，mount("/") 排在最后。
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

    return app


background = Background()

app = create_app()
