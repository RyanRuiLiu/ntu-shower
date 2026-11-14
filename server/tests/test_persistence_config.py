"""新数据层：全局配置与逐设备覆盖。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.persistence import config, devices

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'config.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_global_defaults_to_empty(session: Session) -> None:
    # 库里还没有这一行（全新安装）时对外呈现修订号 1：与旧层 init_tables 预置的
    # 行语义一致，GET 报 1、第一次写入变成 2（契约由 test_global_config_roundtrip 锁定）。
    assert config.get_global(session) == (1, {})


def test_global_roundtrip(session: Session) -> None:
    config.put_global(session, 1, {"session.max_s": 3600})
    assert config.get_global(session) == (1, {"session.max_s": 3600})
    config.put_global(session, 2, {"session.max_s": 7200})
    assert config.get_global(session) == (2, {"session.max_s": 7200})


def test_device_override_roundtrip(session: Session) -> None:
    devices.register(
        session,
        device_id="dev-a",
        app_version="0.6.5",
        version_code=605,
        os_version="4.4.4",
        capabilities=[],
        ip="10.0.0.1",
        booted_at=NOW,
        seen_at=NOW,
    )
    assert config.get_device(session, "dev-a") is None
    config.put_device(session, "dev-a", 1, {"voice.volume": 8})
    assert config.get_device(session, "dev-a") == (1, {"voice.volume": 8})
    config.put_device(session, "dev-a", 2, {"voice.volume": 4})
    assert config.get_device(session, "dev-a") == (2, {"voice.volume": 4})


def test_missing_device_config_is_rejected(session: Session) -> None:
    """未登记设备的配置写入被外键拒绝。

    配置是每台设备的运行状态（FK CASCADE）：设备除档后配置随之消失，不存在
    "未登记也照写"的形态。与审计事实表（无外键、事实保留）刻意不同。
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    assert config.get_device(session, "missing") is None
    with pytest.raises(IntegrityError):
        config.put_device(session, "missing", 1, {"x": 1})
    session.rollback()
    assert config.get_device(session, "missing") is None
