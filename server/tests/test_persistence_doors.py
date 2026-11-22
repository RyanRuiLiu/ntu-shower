"""新数据层：门现态、状态流水、会话与出水段。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.contracts import DoorState, SegmentSource
from app.db.base import Base
from app.db.session import Database
from app.modules.devices import crud as devices
from app.modules.doors import crud as doors

T0 = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'doors.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def _device(session: Session, device_id: str = "dev-a") -> None:
    devices.register(
        session,
        device_id=device_id,
        app_version="0.6.5",
        version_code=605,
        os_version="4.4.4",
        capabilities=[],
        ip="10.0.0.1",
        booted_at=T0,
        seen_at=T0,
    )


def test_live_state_upsert_and_list(session: Session) -> None:
    _device(session)
    doors.upsert_door_live(
        session,
        "dev-a",
        1,
        DoorState.BATHING,
        card_no=7,
        cert_no="c1",
        open_time=T0,
        in_time=T0,
        bath_time_s=30,
        reported_at=T0,
    )
    live = doors.get_door_live(session, "dev-a", 1)
    assert live is not None
    assert live["state"] == "bathing"
    assert doors.get_all_live(session, "dev-a")[1]["card_no"] == 7
    assert doors.get_occupied_by_cert(session, "dev-a", "c1") is not None

    doors.upsert_door_live(session, "dev-a", 1, DoorState.FREE, reported_at=T0 + timedelta(seconds=5))
    assert doors.get_door_live(session, "dev-a", 1)["state"] == "free"  # type: ignore[index]
    assert doors.get_occupied_by_cert(session, "dev-a", "c1") is None
    assert len(doors.list_doors(session, "dev-a")) == 1


def test_session_and_segment_lifecycle(session: Session) -> None:
    _device(session)
    event_id = doors.insert_event(session, T0, "dev-a", 1, DoorState.FREE, DoorState.BATHING, 7, "c1", 0)
    assert event_id > 0

    session_id = doors.open_session(session, "dev-a", 1, 7, "c1", T0, "observed")
    assert session_id > 0
    assert doors.get_open_session(session, "dev-a", 1)["id"] == session_id  # type: ignore[index]
    assert doors.get_active_by_cert(session, "dev-a", "c1") is not None
    assert len(doors.list_open_sessions_of(session, "dev-a")) == 1

    segment_id = doors.open_segment(session, session_id, "dev-a", 1, T0, "12:00")
    assert doors.get_open_segment(session, "dev-a", 1)["id"] == segment_id  # type: ignore[index]
    assert doors.latest_segment_opened_at(session, "dev-a", 1).endswith("Z")

    doors.close_segment(session, segment_id, T0 + timedelta(seconds=90), 90, SegmentSource.MEASURED, "12:01:30")
    doors.touch_session(
        session,
        session_id,
        seen=True,
        at=T0 + timedelta(seconds=90),
        total_flow_s=90,
        device_flow_s=90,
        gap=False,
    )
    doors.close_session(session, session_id, T0 + timedelta(seconds=120), True)

    closed = doors.get_session(session, session_id)
    assert closed is not None
    assert closed["total_flow_s"] == 90
    assert closed["ended_at"].endswith("Z")
    assert doors.get_open_session(session, "dev-a", 1) is None
    assert len(doors.segments_of_session(session, session_id)) == 1

    usage = doors.latest_usage_by_door(session)
    assert usage[("dev-a", 1)]["flow_s"] == 90
    assert usage[("dev-a", 1)]["cert_no"] == "c1"


def test_run_started_at_walks_contiguous_sessions(session: Session) -> None:
    _device(session)
    first = doors.open_session(session, "dev-a", 1, 7, "c1", T0, "observed")
    doors.close_session(session, first, T0 + timedelta(seconds=100), True)
    second = doors.open_session(session, "dev-a", 1, 7, "c1", T0 + timedelta(seconds=160), "observed")
    doors.close_session(session, second, T0 + timedelta(seconds=260), True)
    third = doors.open_session(session, "dev-a", 1, 7, "c1", T0 + timedelta(seconds=900), "observed")
    doors.close_session(session, third, T0 + timedelta(seconds=1000), True)

    # 第二场与第一场间隔 60 秒 ≤ 300，算同一串；第三场距第二场 640 秒，断开。
    # 从最新一场往回走，走到断点即停——这一串只含第三场。
    assert doors.run_started_at(session, "dev-a", 1, "c1", 300) == "2026-11-07T12:15:00.000Z"


def test_derived_segment_and_touch_branches(session: Session) -> None:
    _device(session)
    session_id = doors.open_session(session, "dev-a", 2, 8, "c2", T0, "observed")
    doors.touch_session(
        session,
        session_id,
        seen=False,
        at=T0 + timedelta(seconds=30),
        total_flow_s=10,
        device_flow_s=20,
        gap=True,
    )
    touched = doors.get_session(session, session_id)
    assert touched is not None
    assert touched["observed_span"] == "gap"
    assert touched["total_flow_s"] == 10
    assert touched["last_seen_at"].endswith("Z")

    segment_id = doors.add_derived_segment(session, session_id, "dev-a", 2, T0, T0 + timedelta(seconds=10), 10, "raw")
    assert segment_id > 0
    assert doors.segments_of_session(session, session_id)[0]["source"] == "derived"

    # 不存在时无操作
    doors.close_session(session, 9999, T0, True)

    # 前一场还开着：走到断点就停，不硬往更早走
    doors.open_session(session, "dev-a", 3, 9, "c3", T0, "observed")
    doors.open_session(session, "dev-a", 3, 9, "c3", T0 + timedelta(seconds=60), "observed")
    assert doors.run_started_at(session, "dev-a", 3, "c3", 300).endswith("Z")


def test_missing_device_reads_are_empty(session: Session) -> None:
    """未登记设备的读操作一律空结果；写操作被外键拒绝（不变量落在存储层）。"""
    import pytest
    from sqlalchemy.exc import IntegrityError

    assert doors.get_all_live(session, "missing") == {}
    assert doors.get_door_live(session, "missing", 1) is None
    assert doors.get_occupied_by_cert(session, "missing", "c") is None
    assert doors.get_session(session, 9999) is None
    assert doors.list_open_sessions_of(session, "missing") == []
    assert doors.run_started_at(session, "missing", 1, "", 300) == ""
    assert doors.get_open_segment(session, "missing", 1) is None
    assert doors.latest_segment_opened_at(session, "missing", 1) == ""
    assert doors.latest_usage_by_door(session) == {}
    assert doors.list_doors(session) == []

    with pytest.raises(IntegrityError):
        doors.upsert_door_live(session, "missing", 1, DoorState.FREE, reported_at=T0)
    session.rollback()
    with pytest.raises(IntegrityError):
        doors.insert_event(session, T0, "missing", 1, DoorState.FREE, DoorState.FREE, 0, "", 0)
    session.rollback()
    with pytest.raises(IntegrityError):
        doors.open_session(session, "missing", 1, 0, "", T0, "observed")
    session.rollback()
