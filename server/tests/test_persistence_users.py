"""用户与密钥数据层的持久化契约。

约定与设备凭据同型：摘要存储、明文只在签发瞬间返回、吊销即失效、列表含吊销
历史；用户禁用即时让全部密钥失效。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.auth import crud
from app.db.session import Database
from app.models import Base

NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_user_lifecycle(session: Session) -> None:
    created = crud.create_user(session, "dorm-01", "宿舍系统")
    assert created["username"] == "dorm-01"
    assert created["name"] == "宿舍系统"
    assert created["disabled"] is False
    assert created["has_password"] is False
    assert created["created_at"].endswith("Z")

    updated = crud.update_user(session, "dorm-01", name="改名", password="new-pass-123")
    assert updated is not None
    assert updated["name"] == "改名"
    assert updated["has_password"] is True
    assert updated["disabled"] is False  # 未传不动

    assert crud.update_user(session, "dorm-01", disabled=True) is not None
    assert crud.find_row(session, "dorm-01") is not None
    assert crud.delete_user(session, "dorm-01") is True
    assert crud.delete_user(session, "dorm-01") is False
    assert crud.update_user(session, "ghost", name="x") is None


def test_authenticate_sets_last_login_and_rejects_wrong(session: Session) -> None:
    crud.create_user(session, "admin", password="s3cret-pass")
    assert crud.authenticate(session, "admin", "wrong") is None
    assert crud.authenticate(session, "ghost", "x") is None

    ok = crud.authenticate(session, "admin", "s3cret-pass", now=NOW)
    assert ok is not None
    assert ok["last_login_at"] != ""

    assert crud.update_user(session, "admin", disabled=True) is not None
    assert crud.authenticate(session, "admin", "s3cret-pass") is None


def test_resolve_key_checks_revocation_and_disabled(session: Session) -> None:
    crud.create_user(session, "dorm-01")
    first = crud.issue_key(session, "dorm-01", "a", ["jobs:read"], ["*"])
    second = crud.issue_key(session, "dorm-01", "b", ["jobs:read"], ["*"])
    assert first is not None
    assert second is not None

    resolved = crud.resolve_key(session, first[1])
    assert resolved is not None
    assert resolved[0]["username"] == "dorm-01"
    assert resolved[1]["name"] == "a"

    assert crud.resolve_key(session, "bogus-token") is None

    assert crud.revoke_key(session, "dorm-01", first[0]["id"]) is True
    assert crud.resolve_key(session, first[1]) is None  # 吊销即时失效
    assert crud.resolve_key(session, second[1]) is not None  # 其他密钥不受影响
    assert crud.revoke_key(session, "dorm-01", first[0]["id"]) is False  # 幂等

    crud.update_user(session, "dorm-01", disabled=True)
    assert crud.resolve_key(session, second[1]) is None  # 禁用用户全部密钥失效


def test_resolve_key_updates_last_used_at(session: Session) -> None:
    crud.create_user(session, "dorm-01")
    issued = crud.issue_key(session, "dorm-01", "a", ["jobs:read"], ["*"])
    assert issued is not None
    before = issued[0]["last_used_at"]
    assert before == ""

    resolved = crud.resolve_key(session, issued[1])
    assert resolved is not None
    assert resolved[1]["last_used_at"] != ""


def test_delete_user_cascades_keys(session: Session) -> None:
    crud.create_user(session, "dorm-01")
    issued = crud.issue_key(session, "dorm-01", "a", ["jobs:read"], ["*"])
    assert issued is not None

    crud.delete_user(session, "dorm-01")
    assert crud.resolve_key(session, issued[1]) is None
    assert crud.list_keys(session, "dorm-01") == []
