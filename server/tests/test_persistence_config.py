"""新数据层：全局配置与逐设备覆盖。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import Database
from app.modules.config import crud as config
from app.modules.devices import crud as devices

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'config.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_global_defaults_to_empty(session: Session) -> None:
    # 库里还没有这一行（全新安装）时对外呈现修订号 1，第一次写入变成 2
    # （契约由 test_global_config_roundtrip 锁定）。
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


def test_missing_device_config_write_is_a_no_op(session: Session) -> None:
    """未登记设备的配置写入是 no-op。

    覆盖记在设备影子行上：没有设备就没有可写的行。HTTP 路径到不了这里
    （device_scope_guard 先挡），这一层只是不给自己造行。
    """
    assert config.get_device(session, "missing") is None
    config.put_device(session, "missing", 1, {"x": 1})
    assert config.get_device(session, "missing") is None
