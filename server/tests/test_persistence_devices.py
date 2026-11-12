"""新数据层：设备档案、链路事件、设备日志。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.models.enums import DeviceLogKind, LinkEvent
from app.persistence import devices

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'devices.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def _register(session: Session, device_id: str, seen_at: datetime = NOW) -> None:
    devices.register(
        session,
        device_id=device_id,
        app_version="0.6.5",
        version_code=605,
        os_version="4.4.4",
        capabilities=["session"],
        ip="10.0.0.1",
        booted_at=seen_at - timedelta(seconds=60),
        seen_at=seen_at,
    )


def test_register_appends_and_keeps_position(session: Session) -> None:
    _register(session, "dev-a")
    _register(session, "dev-b")
    assert [record["sort_order"] for record in devices.list_all(session)] == [0, 1]

    # 再次登记只更新版本类字段，位置与名字不变
    assert devices.set_name(session, "dev-a", "1F 东") is True
    session.commit()
    devices.register(
        session,
        device_id="dev-a",
        app_version="0.6.6",
        version_code=606,
        os_version="4.4.4",
        capabilities=["session", "photo"],
        ip="10.0.0.2",
        booted_at=NOW,
        seen_at=NOW,
    )
    record = devices.get(session, "dev-a")
    assert record is not None
    assert record["app_version"] == "0.6.6"
    assert record["capabilities"] == ["session", "photo"]
    assert record["name"] == "1F 东"
    assert record["sort_order"] == 0


def test_list_uses_natural_name_order_on_ties(session: Session) -> None:
    for name in ("10F", "2F", "1F"):
        _register(session, f"dev-{name}")
        devices.set_name(session, f"dev-{name}", name)
    # 手动顺序全部置 0，只剩自然序可依
    session.execute(text("update devices set sort_order = 0"))
    session.commit()
    assert [record["name"] for record in devices.list_all(session)] == ["1F", "2F", "10F"]


def test_set_order_rewrites_positions(session: Session) -> None:
    _register(session, "dev-a")
    _register(session, "dev-b")
    devices.set_order(session, ["dev-b", "dev-a"])
    assert [record["device_id"] for record in devices.list_all(session)] == ["dev-b", "dev-a"]


def test_trusted_and_clock_skew(session: Session) -> None:
    _register(session, "dev-a")
    assert devices.set_trusted(session, "missing", True) is False
    assert devices.set_trusted(session, "dev-a", True) is True
    assert devices.get(session, "dev-a")["trusted"] is True  # type: ignore[index]

    devices.set_clock_skew(session, "dev-a", 1.5)
    assert devices.get_clock_skew(session, "dev-a") == 1.5
    assert devices.get_clock_skew(session, "missing") == 0.0


def test_link_events_are_ordered_and_bounded(session: Session) -> None:
    _register(session, "dev-a")
    for offset, event in ((0, LinkEvent.ONLINE), (10, LinkEvent.OFFLINE), (20, LinkEvent.ONLINE)):
        devices.insert_link_event(session, "dev-a", event, NOW + timedelta(seconds=offset), reason="test")
    events = devices.link_events(session, "dev-a", NOW + timedelta(seconds=15))
    assert [item["event"] for item in events] == ["online", "offline"]
    assert events[0]["occurred_at"].endswith("Z")


def test_missing_device_is_a_noop(session: Session) -> None:
    """档案不存在时，所有写操作都不得凭空造出一行。"""
    assert devices.get(session, "missing") is None
    assert devices.set_name(session, "missing", "x") is False
    assert devices.set_trusted(session, "missing", True) is False
    devices.touch_seen(session, "missing", NOW)
    devices.set_clock_skew(session, "missing", 1.0)
    devices.insert_link_event(session, "missing", LinkEvent.ONLINE, NOW)
    devices.append_device_log(session, "missing", DeviceLogKind.DEVICE, "x", NOW)
    devices.set_order(session, ["missing"])
    assert devices.link_events(session, "missing", NOW) == []
    assert devices.latest_device_log(session, "missing", DeviceLogKind.DEVICE) is None
    assert devices.get_clock_skew(session, "missing") == 0.0
    assert devices.list_all(session) == []


def test_device_log_keeps_latest_n(session: Session) -> None:
    _register(session, "dev-a")
    for index in range(devices.LOG_KEEP + 2):
        devices.append_device_log(
            session,
            "dev-a",
            DeviceLogKind.DEVICE,
            f"line-{index}",
            NOW + timedelta(seconds=index),
        )
    latest = devices.latest_device_log(session, "dev-a", DeviceLogKind.DEVICE)
    assert latest is not None
    assert latest["content"] == f"line-{devices.LOG_KEEP + 1}"

    rows = session.execute(text("select count(*) from device_logs")).scalar_one()
    assert rows == devices.LOG_KEEP
