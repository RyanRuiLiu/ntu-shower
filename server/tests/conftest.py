"""测试隔离：每个测试使用独立临时数据库，连接随用例创建与回收。"""

import pytest

from app.core import config as config_module
from app.core import db as db_module


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_module.close_all_connections()
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    monkeypatch.setattr(config_module.settings, "admin_token", "test-token")
    yield path
    db_module.close_all_connections()


@pytest.fixture
def client(fresh_db):
    """应用客户端：with 上下文持有常驻事件循环（portal）。

    常驻 portal 是必须的，不是风格选择：处理请求的循环注册了需要跨请求存活的
    资源（如 ADB 桥的本地 TCP 监听器）。不进入 with 上下文时每次请求自建
    临时 portal，请求一结束循环即关闭，监听器随之死亡。
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
