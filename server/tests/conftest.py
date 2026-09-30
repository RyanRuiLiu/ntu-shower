"""测试隔离：每个测试使用独立临时数据库，连接缓存随用例重置。"""

import pytest

from app.core import config as config_module
from app.core import db as db_module


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    monkeypatch.setattr(config_module.settings, "admin_token", "test-token")
    monkeypatch.setattr(db_module, "_conns", {})
    return path


@pytest.fixture()
def client(fresh_db):
    """with 上下文启用 portal_factory（portal 同循环测试依赖）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def standalone_client(fresh_db):
    """独立 TestClient：不进入 with 上下文（portal=None），
    每个 websocket_connect 自建独立 portal——ADB 桥等长连接 WebSocket 测试专用，
    避免 WS 会话退出时取消共享 portal 上仍挂起的应用协程（官方语义，
    见 starlette.testclient._portal_factory）。用毕需手动 shutdown 事件循环侧资源。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    test_client = TestClient(create_app())
    test_client.portal = None  # 显式置 None：WS 会话各自起 portal
    return test_client


@pytest.fixture()
def portal(client):
    """with TestClient 上下文注入的运行中 BlockingPortal。"""
    return client.portal
