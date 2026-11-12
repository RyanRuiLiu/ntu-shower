"""新数据层：拨钟状态。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.persistence import devices, extend_hours

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'extend.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def _device(session: Session, device_id: str) -> None:
    devices.register(
        session,
        device_id=device_id,
        app_version="0.6.5",
        version_code=605,
        os_version="4.4.4",
        capabilities=[],
        ip="10.0.0.1",
        booted_at=NOW,
        seen_at=NOW,
    )


def test_record_is_overwriting(session: Session) -> None:
    _device(session, "dev-a")
    extend_hours.record(session, "dev-a", NOW, [{"door_no": 3}])
    extend_hours.record(session, "dev-a", NOW + timedelta(hours=1), [{"door_no": 4}])
    current = extend_hours.get(session, "dev-a")
    assert current is not None
    assert current["assignments"] == [{"door_no": 4}]
    assert current["shifted_at"].endswith("Z")
    assert len(extend_hours.list_shifted(session)) == 1


def test_list_orders_by_shift_time(session: Session) -> None:
    _device(session, "dev-a")
    _device(session, "dev-b")
    extend_hours.record(session, "dev-b", NOW + timedelta(hours=2), [])
    extend_hours.record(session, "dev-a", NOW, [])
    assert [row["device_id"] for row in extend_hours.list_shifted(session)] == ["dev-a", "dev-b"]


def test_clear_removes_row(session: Session) -> None:
    _device(session, "dev-a")
    extend_hours.record(session, "dev-a", NOW, [])
    extend_hours.clear(session, "dev-a")
    assert extend_hours.get(session, "dev-a") is None


def test_missing_device_is_a_noop(session: Session) -> None:
    extend_hours.record(session, "missing", NOW, [])
    extend_hours.clear(session, "missing")
    assert extend_hours.get(session, "missing") is None
    assert extend_hours.list_shifted(session) == []
