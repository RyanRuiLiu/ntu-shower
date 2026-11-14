"""测试隔离与共享夹具：独立临时库、进程级单例清理、标准调用方身份。

五个夹具的组合约定：fresh_db 是根（模板库复制出的独立临时文件），admin_headers
与 ops_headers 在其上登记身份，client 起常驻 portal 的应用（实例全会话共享）；
_reset_process_singletons 与 _isolate_default_db 对每个用例自动生效——后者保证
没声明 fresh_db 的用例也碰不到仓库里的真实库与发布目录。
"""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

# SESSION_SECRET 必须在 app 模块（含 config.Settings 实例化）导入之前就位：
# create_app 在密钥缺失时拒绝启动，这是生产语义，测试环境同样要给出密钥。
os.environ.setdefault("SESSION_SECRET", "test-session-secret")

import pytest
from fastapi.testclient import TestClient

# 表模型分散在各域目录：create_all 前必须全部导入，否则跨域外键解析不到目标表。
# 与 migrations/env.py 同一份清单，漏模型由 tests/test_schema.py 钉住。
from app.auth.models import ApiKey, User  # noqa: F401
from app.core import config as config_module
from app.modules.config.models import AppSettings  # noqa: F401
from app.modules.devices.models import (  # noqa: F401
    Device,
    DeviceClockShift,
    DeviceConfig,
    DeviceCredential,
    DeviceLinkEvent,
    DeviceLog,
)
from app.modules.doors.models import DoorSnapshot, DoorStateEvent  # noqa: F401
from app.modules.ota.models import OtaRelease, OtaReleaseInstall  # noqa: F401
from app.modules.voice.models import VoiceAsset, VoiceDelivery, VoiceRule, VoiceUnit  # noqa: F401
from app.persistence.audit import ApiCall, CommandAudit, JobRun  # noqa: F401
from app.persistence.usage import UsageSegment, UsageSession  # noqa: F401

ADMIN_USER = "admin"
OPS_USER = "ops-user"
TRUSTED_DEVICE_ID = "dev-test-0001"


@pytest.fixture(autouse=True)
def _disable_device_token_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试环境统一关闭设备接入凭据校验。

    生产默认强制校验（见 config.require_device_token），但网关用例关注的是帧协议与
    推导，逐条签发设备凭据只会把无关噪声塞进每个用例。显式关闭属于测试夹具职责，
    不是生产侧的旁路开关；凭据校验本身由 tests/test_auth_endpoints.py 单独验证。
    """
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "require_device_token", False)


@pytest.fixture(autouse=True)
def _reset_process_singletons() -> Iterator[None]:
    """清空进程级单例：跨用例累积产生伪影。

    网关连接表、桥注册表之外，命令等待、事件订阅与按设备分的那组帧锁也是进程内
    状态：应用实例全会话共享（见 client），残留的挂起命令或订阅者会泄漏进下一个用例。

    设备钟偏差同样在此列：它存在模块级字典里，而**它会改变推导结果**（起点由设备报的
    `open_time` 平移而来还是退回观测时刻，取决于有没有偏差）。不清理的话，前一个用例
    留下的偏差会让后一个用例的会话起点凭空平移一段。

    延长服务时间的两份进程内状态也一样。在途的重新分配任务留着，`on_frame` 那条"已经
    有一轮在跑"的互斥判据就永远为真，之后的用例一次都排不进去；而"替他试过却失败了"的
    记忆留着，下一个用例里的人会被直接跳过。
    """
    from app.core import device_clock, events
    from app.core.connections import manager
    from app.modules.bridge.manager import registry
    from app.modules.devices import actions as device_actions
    from app.modules.doors import service as doors_service
    from app.modules.extend_hours import service as extend_hours_service
    from app.modules.voice import service as voice_service

    def clear() -> None:
        manager._connections.clear()
        manager._generations.clear()
        registry.reset()
        doors_service._device_locks.clear()
        device_actions._pending.clear()
        device_actions._config_pending.clear()
        device_actions._channel_waiting.clear()
        device_actions._channel_locks.clear()
        events._subscribers.clear()
        device_clock.clear()
        extend_hours_service._reassign_tasks.clear()
        extend_hours_service._reassign_failed.clear()
        voice_service._tasks.clear()

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _isolate_default_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没带 fresh_db 的用例也碰不到仓库里的真实库与发布目录。

    有些通路把"审计落库"当副作用顺手发生（后台任务的 `_record` 日志与审计表同路双写），
    只验编排、不断言库内容的用例于是也在写库；而 db_path 只有 fresh_db 补丁，没带它的
    用例就把这些行写进了默认路径——开发库。调度器测试每跑一轮漏约 23 行审计进
    `data/ntu-shower.db`，攒起来在网页端像"后台任务全失败"。

    所以隔离下沉为默认：每个用例的库与发布目录都先指到一次性临时文件。需要表结构的
    用例仍必须显式带 fresh_db——临时文件没有表，漏带会当场炸（`no such table`），
    而不是静默写错地方。fresh_db 显式依赖本夹具，覆盖顺序因此是确定的。
    """
    monkeypatch.setattr(config_module.settings, "db_path", str(tmp_path / "undeclared.db"))
    monkeypatch.setattr(config_module.settings, "ota_dir", str(tmp_path / "undeclared-ota"))
    monkeypatch.setattr(config_module.settings, "voice_dir", str(tmp_path / "undeclared-voice"))


@pytest.fixture(scope="session")
def _schema_template(tmp_path_factory: pytest.TempPathFactory) -> str:
    """会话级空库模板：表结构只建一次，之后每个用例复制这份文件。

    每用例执行一遍 init_schema 要 15 毫秒以上，而结果完全相同；复制一个已
    checkpoint 的空库文件是亚毫秒级操作，隔离性不变（每个用例仍是独立文件）。
    """
    base = tmp_path_factory.mktemp("schema-template")
    path = str(base / "template.db")
    from app.db.base import Base
    from app.db.engine import create_db_engine

    engine = create_db_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    # 最后一个连接关闭即 checkpoint，WAL 落盘，模板才是可整文件复制的静止状态
    engine.dispose()
    return path


@pytest.fixture(scope="session")
def openapi_spec() -> dict:
    """对外契约（/openapi.json）：test_permissions 以它对账字典与端点映射。"""
    from app.main import app

    return app.openapi()


@pytest.fixture
def fresh_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _schema_template: str,
    _isolate_default_db: None,
) -> str:
    """换到独立临时库并建好全部表。

    显式依赖 _isolate_default_db：先落到的"未声明"路径之上，再覆盖成本用例的
    test.db——覆盖顺序靠夹具依赖钉死，不靠同一作用域内的声明次序。

    表结构来自会话级模板的整文件复制（见 _schema_template）：隔离性与逐用例
    建表完全等价，成本从 15 毫秒降到亚毫秒。桥端口换到测试段，命令超时收紧到
    0.3 秒，依赖超时的用例不白等；发布目录同入临时区，测试写下的 APK 不会
    碰到真实文件。
    """
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config_module.settings, "db_path", path)
    # 桥端口按 xdist worker 分段：多 worker 并行时共用同一固定端口，A worker 的
    # TCP 客户端会连进 B worker 的桥，帧串位表现为 nonce 错乱（xdist 官方约定
    # worker 编号在 PYTEST_XDIST_WORKER，形如 gw3）。每段 10 个端口，当前
    # bridge 测试的独特设备数 ≤3，余量充分；无 xdist 时回到 gw0 即原端口。
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    monkeypatch.setattr(
        config_module.settings,
        "bridge_base_port",
        25655 + int(worker.removeprefix("gw")) * 10,
    )
    monkeypatch.setattr(config_module.settings, "ota_dir", str(tmp_path / "ota_releases"))
    monkeypatch.setattr(config_module.settings, "voice_dir", str(tmp_path / "voice"))
    monkeypatch.setattr(config_module.settings, "assign_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "settle_timeout_s", 0.3)
    monkeypatch.setattr(config_module.settings, "photo_timeout_s", 0.3)
    # 目录存在性由 create_app 在装配时保证一次；共享应用后每个用例自己补
    Path(config_module.settings.ota_dir).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_schema_template, path)
    return path


@pytest.fixture
def admin_headers(fresh_db: str) -> dict[str, str]:
    """管理台调用方：全部权限密钥（*）——面板即操作台，代操作全部动作。"""
    from app.auth import crud
    from app.db.session import Database

    database = Database(f"sqlite:///{Path(fresh_db).as_posix()}")
    try:
        with database.new_session() as session:
            crud.create_user(session, ADMIN_USER, "测试管理员")
            issued = crud.issue_key(session, ADMIN_USER, "测试密钥", ["*"], ["*"])
    finally:
        database.dispose()
    assert issued is not None
    _, token = issued
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ops_headers(fresh_db: str) -> dict[str, str]:
    """用户面调用方：sessions:write + devices:photo，作用域通配。

    同时登记一台已信任设备 dev-test-0001：用户面操作类接口的守卫链要求设备
    存在且已信任，需要现成目标的用例直接用常量 TRUSTED_DEVICE_ID。
    """
    from app.auth import crud
    from app.db.base import utcnow
    from app.db.session import Database
    from app.modules.devices import crud as devices_store

    database = Database(f"sqlite:///{Path(fresh_db).as_posix()}")
    try:
        with database.new_session() as session:
            devices_store.register(
                session,
                device_id=TRUSTED_DEVICE_ID,
                app_version="0.4.13",
                version_code=57,
                os_version="4.4.4",
                capabilities=[],
                ip="127.0.0.1",
                booted_at=utcnow(),
                seen_at=utcnow(),
            )
            devices_store.set_trusted(session, TRUSTED_DEVICE_ID, True)
            crud.create_user(session, OPS_USER, "测试用")
            issued = crud.issue_key(session, OPS_USER, "测试密钥", ["sessions:write", "devices:photo"], ["*"])
    finally:
        database.dispose()
    assert issued is not None
    _, token = issued
    return {"Authorization": f"Bearer {token}"}


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
