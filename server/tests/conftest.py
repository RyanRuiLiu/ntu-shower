"""测试隔离与共享夹具：独立临时库、进程级单例清理、标准调用方身份。

四个夹具的组合约定：fresh_db 是根（独立临时库并建好全部表），admin_headers 与
ops_headers 在其上登记身份，client 起常驻 portal 的应用；_reset_process_singletons
对每个用例自动生效。
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core import db as db_module

ADMIN_IDENTITY = "admin-identity-0001"
OPS_IDENTITY = "test-identity"
TRUSTED_DEVICE_ID = "dev-test-0001"


@pytest.fixture(autouse=True)
def _reset_process_singletons() -> Iterator[None]:
    """清空网关连接表与桥注册表：进程级单例，跨用例累积产生伪影。"""
    from app.core.connections import manager
    from app.modules.bridge.manager import registry

    def clear() -> None:
        manager._connections.clear()
        registry.reset()

    clear()
    yield
    clear()


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """换到独立临时库并建好全部表。

    建表不能省：不经过 create_app 的用例（服务层纯 asyncio 测试）也要拿到完整
    schema。桥端口换到测试段，命令超时收紧到 0.3 秒，依赖超时的用例不白等；
    发布目录同入临时区，测试写下的 APK 不会碰到真实文件。
    """
    from app.main import init_schema

    db_module.close_all_connections()
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    monkeypatch.setattr(config_module.settings, "bridge_base_port", 25655)
    monkeypatch.setattr(config_module.settings, "ota_dir", str(tmp_path / "ota_releases"))
    monkeypatch.setattr(config_module.settings, "assign_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "settle_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "photo_timeout_s", 0.3)
    init_schema()
    yield path
    db_module.close_all_connections()


@pytest.fixture
def admin_headers(fresh_db: str) -> dict[str, str]:
    """管理台调用方：admin 之外还持 session/photo——管理台要代操作用户动作。"""
    from app.modules.access import repository as access_repository
    from app.modules.access.identity import IdentityKind

    access_repository.put_identity(
        ADMIN_IDENTITY, IdentityKind.WEB, ["admin", "session", "photo"], "测试管理员", device_scope=["*"]
    )
    return {"X-Identity": ADMIN_IDENTITY}


@pytest.fixture
def ops_headers(fresh_db: str) -> dict[str, str]:
    """用户面调用方：session + photo 能力位，作用域通配。

    同时登记一台已信任设备 dev-test-0001：用户面操作类接口的守卫链要求设备
    存在且已信任，需要现成目标的用例直接用常量 TRUSTED_DEVICE_ID。
    """
    from app.modules.access import repository as access_repository
    from app.modules.access.identity import IdentityKind
    from app.modules.devices import repository as devices_repository

    devices_repository.register(TRUSTED_DEVICE_ID, "0.4.13", 57, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    devices_repository.set_trusted(TRUSTED_DEVICE_ID, True)
    access_repository.put_identity(OPS_IDENTITY, IdentityKind.WEB, ["session", "photo"], "测试用", device_scope=["*"])
    return {"X-Identity": OPS_IDENTITY}


@pytest.fixture
def client(fresh_db: str) -> Iterator[TestClient]:
    """应用客户端：with 上下文持有常驻事件循环（portal）。

    常驻 portal 是必须的：处理请求的循环上注册了需要跨请求存活的资源（如 ADB 桥
    的本地 TCP 监听器），每次请求自建临时 portal 会让它们随循环一起消失。
    """
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
