"""测试隔离：每个测试使用独立临时数据库，连接随用例创建与回收。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core import db as db_module


@pytest.fixture(autouse=True)
def _reset_process_singletons() -> Iterator[None]:
    """进程级单例的用例隔离：网关连接表与桥接注册表。

    桥接注册表持有端口分配与世代计数，跨用例累积会让后续用例拿到别的端口。
    """
    from app.gateway.manager import manager as gateway_manager
    from app.modules.bridge.manager import registry

    def clear() -> None:
        gateway_manager._connections.clear()
        registry.reset()

    clear()
    yield
    clear()


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    db_module.close_all_connections()
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    monkeypatch.setattr(config_module.settings, "admin_token", "test-token")
    # 桥接端口基址换成测试段：默认段是生产环境值，本机 SSH 隧道/其他实例
    # 占用该端口时会让桥接用例失败（已发生过两次）
    monkeypatch.setattr(config_module.settings, "bridge_base_port", 25655)
    # 命令等待超时收紧：依赖超时的用例不应白等真实的一分钟
    monkeypatch.setattr(config_module.settings, "assign_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "settle_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "photo_timeout_s", 0.3)
    yield path
    db_module.close_all_connections()


@pytest.fixture
def ops_headers(fresh_db: str) -> dict[str, str]:
    """已获授 `ops` + `photo` 的调用方请求头。

    操作类接口要求身份标识（`X-Identity`），需要调用它们的用例用这个夹具，
    不要去探名单表的结构。
    """
    from app.modules.access import repository as access_repository
    from app.modules.access.identity import IdentityKind

    access_repository.put_grant("test-identity", IdentityKind.WEB, ["ops", "photo"], "测试用")
    return {"X-Identity": "test-identity"}


@pytest.fixture
def client(fresh_db: str) -> Iterator[TestClient]:
    """应用客户端：with 上下文持有常驻事件循环（portal）。

    常驻 portal 是必须的，不是风格选择：处理请求的循环注册了需要跨请求存活的
    资源（如 ADB 桥的本地 TCP 监听器）。不进入 with 上下文时每次请求自建
    临时 portal，请求一结束循环即关闭，监听器随之死亡。

    create_app 延后到用例内导入：避免测试收集阶段就构造应用（建库、起后台任务）。
    """
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
