"""ntu-shower 服务端入口：装配设备网关平面与各领域模块。

- gateway            设备 WS 网关（/gateway/device）
- modules.doors      喷头现态（/api/v1/doors，含 /api/v1/stream 实时推送）
- modules.sessions   分配 / 结算 / 拍照（/api/v1/sessions）与命令审计（/ops/commands）
- modules.shadow     设备影子（reported / desired / metadata）
- modules.access     身份与能力位（/api/v1/identity、/ops/identities）
- modules.devices    设备注册表（/ops/devices）与远程操作（exec / capture / ui.*）
- modules.config     远程配置（/ops/config、/ops/devices/{id}/config）
- modules.ota        版本清单 / 下载 / 发布（/ota/*、/ops/releases）
- modules.bridge     真实 ADB 桥接（/ops/devices/{id}/bridge/*，经 443 透传）

网页端（`web/`）挂在根路径：合并自旧系统的调试面板与画面查看器，零构建静态文件。
服务端同时是 API 与这个界面的宿主，因此入口只有一个。

访问控制见 core/security 与 modules/access：查看类接口无需凭据，
操作类需 `X-Identity` 标识 + 能力位，管理面用 `X-Ops-Token`（docs/DECISIONS.md）。
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from typing_extensions import TypedDict

from .core import db
from .core.config import settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging
from .core.security import assert_ops_token_configured
from .core.version import APP_VERSION
from .gateway.routes import router as gateway_router
from .modules.access import repository as access_repository
from .modules.access.routes import ops_router as access_ops_router
from .modules.access.routes import router as access_router
from .modules.bridge.routes import ops_router as bridge_ops_router
from .modules.bridge.routes import router as bridge_router
from .modules.config import store as config_store
from .modules.config.routes import ops_router as config_ops_router
from .modules.devices import repository as devices_repository
from .modules.devices.routes import ops_router as devices_ops_router
from .modules.doors import repository as doors_repository
from .modules.doors.routes import ops_router as doors_ops_router
from .modules.doors.routes import router as doors_router
from .modules.ota import repository as ota_repository
from .modules.ota.routes import ops_router as ota_ops_router
from .modules.ota.routes import router as ota_router
from .modules.sessions import auto_settle
from .modules.sessions import repository as sessions_repository
from .modules.sessions.routes import ops_router as sessions_ops_router
from .modules.sessions.routes import router as sessions_router
from .modules.shadow.routes import ops_router as shadow_ops_router
from .modules.shadow.routes import router as shadow_router

#: 要求浏览器每次回来验证的前端资源后缀。
STATIC_SUFFIXES = (".html", ".js", ".css")


class HealthStatus(TypedDict):
    """存活探针的应答；`version` 让运维能一眼确认线上跑的是哪一版。"""

    status: str
    name: str
    version: str


def init_schema() -> None:
    """建全部表。

    各领域仓储持有自己的表结构，这里是**唯一**的装配点：应用启动与测试夹具都调它。
    这段原先内联在 `create_app` 里，于是不经过应用启动的用例（服务层的纯 asyncio 测试）
    会连到默认路径那个库——而它的 schema 是旧的，报出来是"列不存在"而不是"表不存在"，
    看着像代码错了，实则是那些用例根本没建表。

    全部用 `CREATE TABLE IF NOT EXISTS`，重复调用没有副作用。
    """
    db.init_db()
    devices_repository.init_tables()
    doors_repository.init_tables()
    sessions_repository.init_tables()
    access_repository.init_tables()
    ota_repository.init_tables()
    config_store.init_tables()


def create_app() -> FastAPI:
    # 先装配日志：此后的初始化失败才有痕迹可查
    configure_logging(settings.log_level)
    init_schema()
    Path(settings.ota_dir).mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 启动自检：配置不合法时拒绝启动，而非带着占位密钥对外服务
        assert_ops_token_configured()
        auto_settle.start()
        yield
        auto_settle.stop()

    app = FastAPI(title="NTU Shower", version=APP_VERSION, lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(gateway_router)
    app.include_router(doors_router)
    app.include_router(doors_ops_router)
    app.include_router(sessions_router)
    app.include_router(sessions_ops_router)
    app.include_router(shadow_router)
    app.include_router(shadow_ops_router)
    app.include_router(devices_ops_router)
    app.include_router(config_ops_router)
    app.include_router(ota_router)
    app.include_router(ota_ops_router)
    app.include_router(bridge_router)
    app.include_router(bridge_ops_router)
    app.include_router(access_router)
    app.include_router(access_ops_router)

    @app.get("/healthz", tags=["meta"])
    def health() -> HealthStatus:
        return {"status": "ok", "name": "ntu-shower", "version": APP_VERSION}

    # 网页端挂在根路径：它就是这个服务唯一的人机界面，而 API 在 /api、/ops、
    # /ota、/gateway 下，路由已在前注册，不会被静态文件抢走。
    #
    # 必须放在最后注册：Starlette 按注册顺序匹配，mount("/") 排在最后。
    web_dir = Path(__file__).resolve().parent.parent / "web"
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    # 前端资源要求浏览器每次都回来验一遍。
    #
    # `no-cache` **不是**"不缓存"：浏览器仍会带上 ETag 来问，没变就是 304（很快），
    # 变了才重新下载。没有它时 Starlette 只给 `Last-Modified`，浏览器据此走启发式
    # 缓存——改完样式刷新，看到的还是旧的那一份，得手动强刷才行。
    @app.middleware("http")
    async def revalidate_web_assets(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        if request.url.path.endswith(STATIC_SUFFIXES):
            response.headers["Cache-Control"] = "no-cache"
        return response

    return app


app = create_app()
