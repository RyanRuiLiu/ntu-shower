"""ntu-shower 服务端入口：装配设备网关平面与各领域模块。

- gateway            设备 WS 网关（/gateway/device）
- modules.doors      用户 API：喷头现态（/api/v1/doors，含 /api/v1/stream 实时推送）
- modules.sessions   用户 API：分配/结算/拍照（/api/v1/sessions、/api/v1/photos）
- modules.shadow     用户/管理 API：设备影子（/api/v1 与 /admin 的 shadow 端点）
- modules.devices    管理 API：设备注册表（/admin/devices）
- modules.config     管理 API：远程配置（/admin/config、/admin/devices/{id}/config）
- modules.ota        设备/管理 API：清单下载与发布（/ota/*、/admin/releases）
- modules.bridge     管理 API：真实 ADB 桥接（/admin/devices/{id}/adb/*，经 443 透传）
- web                前端页面（/web/*，零构建静态页）

鉴权语义见 core/security：匿名只读、user 可操作设备、admin 全功能。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from typing_extensions import TypedDict

from .core import db
from .core.config import settings
from .core.errors import install_error_handlers
from .core.logging import configure_logging
from .core.security import assert_admin_token_configured
from .core.version import APP_VERSION
from .gateway.routes import router as gateway_router
from .modules.access import repository as access_repository
from .modules.access.routes import admin_router as access_admin_router
from .modules.access.routes import router as access_router
from .modules.bridge.routes import admin_router as bridge_admin_router
from .modules.bridge.routes import router as bridge_router
from .modules.config import store as config_store
from .modules.config.routes import admin_router as config_admin_router
from .modules.devices import repository as devices_repository
from .modules.devices.routes import admin_router as devices_admin_router
from .modules.doors import repository as doors_repository
from .modules.doors.routes import router as doors_router
from .modules.ota import repository as ota_repository
from .modules.ota.routes import admin_router as ota_admin_router
from .modules.ota.routes import router as ota_router
from .modules.sessions import auto_settle
from .modules.sessions import repository as sessions_repository
from .modules.sessions.routes import admin_router as sessions_admin_router
from .modules.sessions.routes import router as sessions_router
from .modules.shadow.routes import admin_router as shadow_admin_router
from .modules.shadow.routes import router as shadow_router


class HealthStatus(TypedDict):
    """存活探针的应答；`version` 让运维能一眼确认线上跑的是哪一版。"""

    status: str
    name: str
    version: str


def create_app() -> FastAPI:
    # 先装配日志：此后的初始化失败才有痕迹可查
    configure_logging(settings.log_level)
    db.init_db()
    devices_repository.init_tables()
    doors_repository.init_tables()
    sessions_repository.init_tables()
    access_repository.init_tables()
    ota_repository.init_tables()
    config_store.init_tables()
    Path(settings.ota_dir).mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # 启动自检：配置不合法时拒绝启动，而非带着占位密钥对外服务
        assert_admin_token_configured()
        auto_settle.start()
        yield
        auto_settle.stop()

    app = FastAPI(title="NTU Shower", version=APP_VERSION, lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(gateway_router)
    app.include_router(doors_router)
    app.include_router(sessions_router)
    app.include_router(sessions_admin_router)
    app.include_router(shadow_router)
    app.include_router(shadow_admin_router)
    app.include_router(devices_admin_router)
    app.include_router(config_admin_router)
    app.include_router(ota_router)
    app.include_router(ota_admin_router)
    app.include_router(bridge_router)
    app.include_router(bridge_admin_router)
    app.include_router(access_router)
    app.include_router(access_admin_router)

    web_dir = Path(__file__).resolve().parent.parent / "web"
    app.mount("/web", StaticFiles(directory=str(web_dir)), name="web")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/web/index.html")

    @app.get("/app", include_in_schema=False)
    def app_page() -> RedirectResponse:
        return RedirectResponse("/web/index.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page() -> RedirectResponse:
        return RedirectResponse("/web/console.html")

    @app.get("/healthz", tags=["meta"])
    def health() -> HealthStatus:
        return {"status": "ok", "name": "ntu-shower", "version": APP_VERSION}

    return app


app = create_app()
