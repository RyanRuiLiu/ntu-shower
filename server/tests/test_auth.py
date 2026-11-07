"""认证基元与管理台账号、设备凭据。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.auth import passwords, tokens
from app.db.session import Database
from app.models import Base
from app.persistence import device_credentials, devices, users

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_password_hash_roundtrip() -> None:
    encoded = passwords.hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert passwords.verify_password(encoded, "correct horse battery staple") is True
    assert passwords.verify_password(encoded, "wrong") is False
    assert passwords.verify_password("not-a-hash", "x") is False


def test_token_helpers() -> None:
    token = tokens.generate_token()
    assert len(token) >= 40
    assert tokens.hash_token(token) == tokens.hash_token(token)
    assert tokens.hash_token(token) != token
    assert tokens.extract_bearer(f"Bearer {token}") == token
    assert tokens.extract_bearer("bearer  spaced ") == "spaced"
    assert tokens.extract_bearer("Basic abc") is None
    assert tokens.extract_bearer(None) is None


def test_user_lifecycle(session: Session) -> None:
    created = users.create_user(session, "admin", "s3cret", ["admin"])
    assert created["capabilities"] == ["admin"]
    assert created["device_scope"] == ["*"]
    assert "password_hash" not in created

    assert users.authenticate(session, "admin", "wrong") is None
    ok = users.authenticate(session, "admin", "s3cret", now=NOW)
    assert ok is not None
    assert ok["last_login_at"] == NOW

    assert users.set_disabled(session, "admin", True) is True
    assert users.authenticate(session, "admin", "s3cret") is None
    assert users.set_password(session, "admin", "new-pass") is True
    assert users.set_disabled(session, "admin", False) is True
    assert users.authenticate(session, "admin", "new-pass") is not None
    assert users.authenticate(session, "ghost", "x") is None


def test_device_credential_lifecycle(session: Session) -> None:
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
    token = device_credentials.issue(session, "dev-a", label="首发")
    assert token is not None
    assert device_credentials.authenticate_device(session, token, now=NOW) == "dev-a"
    assert device_credentials.authenticate_device(session, "bogus", now=NOW) is None

    assert device_credentials.revoke(session, token, now=NOW) is True
    assert device_credentials.authenticate_device(session, token, now=NOW) is None
    assert device_credentials.revoke(session, token, now=NOW) is False


def test_expired_credential_is_rejected(session: Session) -> None:
    devices.register(
        session,
        device_id="dev-b",
        app_version="0.6.5",
        version_code=605,
        os_version="4.4.4",
        capabilities=[],
        ip="10.0.0.1",
        booted_at=NOW,
        seen_at=NOW,
    )
    token = device_credentials.issue(session, "dev-b", expires_at=NOW + timedelta(days=1))
    assert token is not None
    assert device_credentials.authenticate_device(session, token, now=NOW) == "dev-b"
    assert device_credentials.authenticate_device(session, token, now=NOW + timedelta(days=2)) is None


def test_issue_for_missing_device_returns_none(session: Session) -> None:
    assert device_credentials.issue(session, "missing") is None
