"""认证基元与管理台账号、设备凭据。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core import passwords, tokens
from app.db.session import Database
from app.models import Base
from app.persistence import devices, principals

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
    created = principals.create_account(session, "admin", "s3cret", ["admin"])
    assert created["capabilities"] == ["admin"]
    assert created["device_scope"] == ["*"]
    assert "password_hash" not in created

    assert principals.authenticate(session, "admin", "wrong") is None
    ok = principals.authenticate(session, "admin", "s3cret", now=NOW)
    assert ok is not None
    assert ok["last_seen_at"] == NOW

    assert principals.set_disabled(session, "admin", True) is True
    assert principals.authenticate(session, "admin", "s3cret") is None
    assert principals.set_password(session, "admin", "new-pass") is True
    assert principals.set_disabled(session, "admin", False) is True
    assert principals.authenticate(session, "admin", "new-pass") is not None
    assert principals.authenticate(session, "ghost", "x") is None


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
    token = devices.issue(session, "dev-a", label="首发")
    assert token is not None
    assert devices.authenticate_device(session, token) == "dev-a"
    assert devices.authenticate_device(session, "bogus") is None

    assert devices.revoke(session, token, now=NOW) is True
    assert devices.authenticate_device(session, token) is None
    assert devices.revoke(session, token, now=NOW) is False


def test_issue_takes_no_expiry_argument(session: Session) -> None:
    """凭据没有过期语义：签发→吊销两态，不存在第三态。

    旧设计有 expires_at（生产从未使用）；删除后签名上也不该再有它——
    这条防止它在调用侧以任何形式复活。
    """
    import pytest

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
    token = devices.issue(session, "dev-b")
    assert token is not None
    assert devices.authenticate_device(session, token) == "dev-b"
    assert devices.revoke(session, token) is True
    assert devices.authenticate_device(session, token) is None
    with pytest.raises(TypeError):
        devices.issue(session, "dev-b", expires_at=NOW + timedelta(days=1))


def test_issue_for_missing_device_returns_none(session: Session) -> None:
    assert devices.issue(session, "missing") is None
