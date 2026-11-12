"""设备接入凭据的轮换持久化：只存摘要，吊销写 revoked_at。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.persistence import device_credentials, devices

DEVICE_ID = "dev-credential"


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'credentials.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        now = datetime.now(UTC)
        devices.register(
            db_session,
            device_id=DEVICE_ID,
            app_version="0.6.5",
            version_code=605,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=now,
            seen_at=now,
        )
        yield db_session
    database.dispose()


def test_issue_returns_plaintext_once_and_stores_only_digest(session: Session) -> None:
    token = device_credentials.issue(session, DEVICE_ID, label="切换补发")
    assert token is not None
    assert device_credentials.authenticate_device(session, token) == DEVICE_ID

    listed = device_credentials.list_for_device(session, DEVICE_ID)
    assert len(listed) == 1
    assert listed[0]["label"] == "切换补发"
    assert "token" not in listed[0]


def test_revoke_all_invalidates_every_credential(session: Session) -> None:
    first = device_credentials.issue(session, DEVICE_ID)
    second = device_credentials.issue(session, DEVICE_ID)
    assert first is not None
    assert second is not None
    assert first != second
    assert device_credentials.revoke_all(session, DEVICE_ID) == 2
    assert device_credentials.authenticate_device(session, first) is None
    assert device_credentials.authenticate_device(session, second) is None
    assert device_credentials.list_for_device(session, DEVICE_ID) == []


def test_issue_for_unknown_device_returns_none(session: Session) -> None:
    assert device_credentials.issue(session, "no-such-device") is None
    assert device_credentials.revoke_all(session, "no-such-device") == 0
