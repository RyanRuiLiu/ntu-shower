"""新数据层：标识名单的持久化契约。

与旧实现逐条对齐：
- 登记即全量覆盖，`device_scope` 缺省为全部设备
- 更新时不改 `kind`、不改 `created_at`
- 列表按登记时间倒序
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.session import Database
from app.models import Base
from app.modules.access.identity import IdentityKind
from app.persistence import principals as access


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'access.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_put_creates_with_defaults(session: Session) -> None:
    record = access.put(session, "web-001", IdentityKind.WEB, ["admin"])
    assert record["identity"] == "web-001"
    assert record["kind"] is IdentityKind.WEB
    assert record["capabilities"] == ["admin"]
    assert record["device_scope"] == ["*"]
    assert record["label"] == ""
    assert record["created_at"].endswith("Z")


def test_put_updates_without_touching_kind_or_created_at(session: Session) -> None:
    first = access.put(session, "web-002", IdentityKind.WEB, ["session"], label="a")
    second = access.put(
        session,
        "web-002",
        IdentityKind.OPENID,
        ["admin", "stats"],
        label="b",
        device_scope=["dev-1"],
    )
    assert second["kind"] is IdentityKind.WEB
    assert second["created_at"] == first["created_at"]
    assert second["capabilities"] == ["admin", "stats"]
    assert second["device_scope"] == ["dev-1"]
    assert second["label"] == "b"


def test_find_missing_returns_none(session: Session) -> None:
    assert access.find(session, "nobody") is None


def test_list_orders_newest_first(session: Session) -> None:
    access.put(session, "first", IdentityKind.WEB, ["session"])
    access.put(session, "second", IdentityKind.WEB, ["session"])
    identities = [record["identity"] for record in access.list_all(session)]
    assert identities == ["second", "first"]


def test_delete_reports_whether_row_existed(session: Session) -> None:
    access.put(session, "temp", IdentityKind.WEB, ["session"])
    assert access.delete(session, "temp") is True
    assert access.delete(session, "temp") is False
    assert access.find(session, "temp") is None
