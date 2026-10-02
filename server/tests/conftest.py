"""测试隔离与共享夹具：独立临时库、进程级单例清理、标准调用方身份。

四个夹具的组合约定：fresh_db 是根（模板库复制出的独立临时文件），admin_headers
与 ops_headers 在其上登记身份，client 起常驻 portal 的应用（实例全会话共享）；
_reset_process_singletons 对每个用例自动生效。
"""

import shutil
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
    """清空进程级单例：跨用例累积产生伪影。

    网关连接表、桥注册表之外，命令等待、事件订阅与按设备分的那组帧锁也是进程内
    状态：应用实例全会话共享（见 client），残留的挂起命令或订阅者会泄漏进下一个用例。

    设备钟偏差同样在此列：它存在模块级字典里，而**它会改变推导结果**（起点由设备报的
    `open_time` 平移而来还是退回观测时刻，取决于有没有偏差）。不清理的话，前一个用例
    留下的偏差会让后一个用例的会话起点凭空平移一段。
    """
    from app.core import device_clock, events
    from app.core.connections import manager
    from app.modules.bridge.manager import registry
    from app.modules.doors import service as doors_service
    from app.modules.sessions import service as sessions_service

    def clear() -> None:
        manager._connections.clear()
        registry.reset()
        doors_service._device_locks.clear()
        sessions_service._pending.clear()
        sessions_service._config_pending.clear()
        sessions_service._channel_waiting.clear()
        events._subscribers.clear()
        device_clock.clear()

    clear()
    yield
    clear()


@pytest.fixture(scope="session")
def _schema_template(tmp_path_factory: pytest.TempPathFactory) -> str:
    """会话级空库模板：表结构只建一次，之后每个用例复制这份文件。

    每用例执行一遍 init_schema 要 15 毫秒以上，而结果完全相同；复制一个已
    checkpoint 的空库文件是亚毫秒级操作，隔离性不变（每个用例仍是独立文件）。
    """
    from app.main import init_schema

    base = tmp_path_factory.mktemp("schema-template")
    path = str(base / "template.db")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_module.settings, "db_path", path)
        mp.setattr(config_module.settings, "ota_dir", str(base / "ota_releases"))
        init_schema()
    db_module.close_all_connections()  # 最后一个连接关闭即 checkpoint，WAL 落盘
    return path


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _schema_template: str) -> Iterator[str]:
    """换到独立临时库并建好全部表。

    表结构来自会话级模板的整文件复制（见 _schema_template）：隔离性与逐用例
    建表完全等价，成本从 15 毫秒降到亚毫秒。桥端口换到测试段，命令超时收紧到
    0.3 秒，依赖超时的用例不白等；发布目录同入临时区，测试写下的 APK 不会
    碰到真实文件。
    """
    db_module.close_all_connections()
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    monkeypatch.setattr(config_module.settings, "bridge_base_port", 25655)
    monkeypatch.setattr(config_module.settings, "ota_dir", str(tmp_path / "ota_releases"))
    monkeypatch.setattr(config_module.settings, "assign_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "settle_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "photo_timeout_s", 0.3)
    # 目录存在性由 create_app 在装配时保证一次；共享应用后每个用例自己补
    Path(config_module.settings.ota_dir).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_schema_template, path)
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

    应用实例使用 `app.main` 导入时构建的那一个，全会话共享，不再逐用例重建：
    应用对数据库路径无状态（连接在请求时按 settings 解析），逐用例换库文件
    已提供隔离，重建实例纯属每个用例几十毫秒的白花开销。

    常驻 portal 是必须的：处理请求的循环上注册了需要跨请求存活的资源（如 ADB 桥
    的本地 TCP 监听器），每次请求自建临时 portal 会让它们随循环一起消失。
    """
    from app.main import app

    with TestClient(app) as c:
        yield c
