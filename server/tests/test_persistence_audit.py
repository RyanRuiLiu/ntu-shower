"""新数据层：命令审计与后台任务记录。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.models.enums import BackgroundOutcome, CommandStatus
from app.persistence import audit, devices

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
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
        booted_at=NOW,
        seen_at=NOW,
    )


def test_command_lifecycle(session: Session) -> None:
    _device(session)
    audit.insert_command(session, "cmd-1", "dev-a", "session.assign", NOW, trace_id="t-1", params={"door_no": 3})
    pending = audit.list_commands(session, "dev-a")
    assert pending[0]["status"] is None
    assert pending[0]["params"] == '{"door_no": 3}'
    assert audit.count_pending(session) == 1

    audit.finish(
        session,
        "cmd-1",
        CommandStatus.OK,
        "",
        "done",
        NOW,
        data={"image_base64": "x"},
    )
    done = audit.list_commands(session, "dev-a")[0]
    assert done["status"] == "ok"
    assert done["data"] == '{"image_base64": "x"}'
    assert audit.count_pending(session) == 0


def test_abort_pending_closes_leftovers(session: Session) -> None:
    _device(session)
    audit.insert_command(session, "cmd-1", "dev-a", "session.assign", NOW)
    audit.insert_command(session, "cmd-2", "dev-a", "session.assign", NOW)
    assert audit.abort_pending(session, NOW) == 2
    assert all(item["status"] == "failed" for item in audit.list_commands(session))


def test_background_task_runs_are_ordered_and_filtered(session: Session) -> None:
    _device(session, "dev-a")
    _device(session, "dev-b")
    audit.insert_background_task_run(session, "dev-a", 1, "c1", "assign", BackgroundOutcome.ASSIGNED, NOW)
    audit.insert_background_task_run(session, "dev-b", 2, "c2", "settle", BackgroundOutcome.SETTLED, NOW)
    every = audit.list_background_task_runs(session)
    assert [item["device_id"] for item in every] == ["dev-b", "dev-a"]
    only_a = audit.list_background_task_runs(session, "dev-a")
    assert len(only_a) == 1
    assert only_a[0]["outcome"] == "assigned"


def test_missing_device_is_a_noop(session: Session) -> None:
    audit.insert_command(session, "cmd-x", "missing", "session.assign", NOW)
    audit.insert_background_task_run(session, "missing", 1, "c", "assign", BackgroundOutcome.SKIPPED, NOW)
    audit.finish(session, "cmd-x", CommandStatus.OK, "", "", NOW)
    assert audit.list_commands(session) == []
    assert audit.list_background_task_runs(session) == []
    assert audit.abort_pending(session, NOW) == 0
