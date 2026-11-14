"""新数据层：OTA 版本与安装确认。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import Database
from app.modules.devices import crud as devices
from app.modules.ota import crud as ota

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'ota.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def _device(session: Session, device_id: str = "dev-a") -> None:
    devices.register(
        session,
        device_id=device_id,
        app_version="0.6.4",
        version_code=604,
        os_version="4.4.4",
        capabilities=[],
        ip="10.0.0.1",
        booted_at=NOW,
        seen_at=NOW,
    )


def test_insert_and_list_ordered_by_version_code(session: Session) -> None:
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, False, "all")
    ota.insert_release(session, "0.6.6", 606, "b" * 64, True, False, "all")
    assert [item["version"] for item in ota.list_releases(session)] == ["0.6.6", "0.6.5"]


def test_latest_for_returns_newer_release(session: Session) -> None:
    _device(session)
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, False, "all")
    latest = ota.latest_for(session, "dev-a", 604)
    assert latest is not None
    assert latest["version"] == "0.6.5"
    assert ota.latest_for(session, "dev-a", 605) is None


def test_force_release_stops_after_install(session: Session) -> None:
    _device(session)
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, True, "all")
    assert ota.latest_for(session, "dev-a", 605) is not None
    ota.mark_installed_upto(session, "dev-a", 605)
    assert ota.is_installed(session, "dev-a", "0.6.5") is True
    assert ota.latest_for(session, "dev-a", 605) is None


def test_target_whitelist(session: Session) -> None:
    _device(session, "dev-a")
    _device(session, "dev-b")
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, False, "dev-b")
    assert ota.latest_for(session, "dev-a", 604) is None
    assert ota.latest_for(session, "dev-b", 604) is not None


def test_rearm_makes_force_applicable_again(session: Session) -> None:
    _device(session)
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, True, "all")
    ota.mark_installed_upto(session, "dev-a", 605)
    assert ota.rearm(session, "dev-a", "0.6.5") is True
    assert ota.latest_for(session, "dev-a", 605) is not None
    assert ota.rearm(session, "dev-a", "0.6.5") is False


def test_delete_release(session: Session) -> None:
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, False, "all")
    assert ota.delete_release(session, "0.6.5") is True
    assert ota.delete_release(session, "0.6.5") is False
    assert ota.list_releases(session) == []


def test_missing_device_is_a_noop(session: Session) -> None:
    ota.insert_release(session, "0.6.5", 605, "a" * 64, False, True, "all")
    ota.mark_installed_upto(session, "missing", 605)
    assert ota.is_installed(session, "missing", "0.6.5") is False
    assert ota.rearm(session, "missing", "0.6.5") is False
    assert ota.latest_for(session, "missing", 604) is not None
