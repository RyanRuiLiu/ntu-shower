"""新数据层：语音素材、规则与投递。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import Database
from app.modules.devices import crud as devices
from app.modules.voice import crud as voice

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = Database(f"sqlite:///{tmp_path / 'voice.db'}")
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


def test_asset_and_unit_lifecycle(session: Session) -> None:
    voice.insert_asset(session, "a1", "数字一", 1, 800, 4000)
    assert [item["asset_id"] for item in voice.list_assets(session)] == ["a1"]
    assert voice.get_asset_by_digit(session, 1)["name"] == "数字一"  # type: ignore[index]
    assert voice.get_asset_by_name(session, "数字一")["asset_id"] == "a1"  # type: ignore[index]
    assert voice.rename_asset(session, "a1", "壹") is True

    voice.insert_unit(session, "digit.1", "a1")
    assert voice.unit_rows(session)["digit.1"]["asset_id"] == "a1"
    assert voice.get_unit_by_asset(session, "a1")["unit"] == "digit.1"  # type: ignore[index]
    assert voice.asset_rows_by_ids(session, ["a1", "missing"]).keys() == {"a1"}

    voice.delete_unit(session, "a1")
    assert voice.unit_rows(session) == {}
    assert voice.delete_asset(session, "a1") is True
    assert voice.delete_asset(session, "a1") is False


def test_rule_lifecycle(session: Session) -> None:
    rule_id = voice.insert_rule(
        session,
        devices=["*"],
        certs=["*"],
        events=["assigned"],
        time_windows=[],
        exempt=[],
        segments=[{"kind": "asset", "asset_id": "a1"}],
        volume=8,
        enabled=True,
        created_at=NOW,
    )
    assert len(voice.list_rules(session)) == 1
    assert len(voice.list_enabled_rules(session)) == 1
    assert voice.get_rule(session, rule_id)["volume"] == 8  # type: ignore[index]

    assert voice.set_enabled(session, rule_id, False, NOW) is True
    assert voice.list_enabled_rules(session) == []

    assert (
        voice.update_rule(
            session,
            rule_id,
            devices=["dev-a"],
            certs=["*"],
            events=["paused"],
            time_windows=[],
            exempt=["c9"],
            segments=["a1", "a2"],
            volume=4,
            enabled=True,
            updated_at=NOW,
        )
        is True
    )
    assert voice.get_rule(session, rule_id)["volume"] == 4  # type: ignore[index]
    assert voice.delete_rule(session, rule_id) is True
    assert voice.delete_rule(session, rule_id) is False


def test_delivery_is_idempotent(session: Session) -> None:
    _device(session)
    rule_id = voice.insert_rule(
        session,
        devices=["*"],
        certs=["*"],
        events=["assigned"],
        time_windows=[],
        exempt=[],
        segments=[{"kind": "asset", "asset_id": "a1"}],
        volume=8,
        enabled=True,
        created_at=NOW,
    )
    first = voice.claim_delivery(
        session,
        event_key="evt-1",
        rule_id=rule_id,
        device_id="dev-a",
        door_no=3,
        cert_no="c1",
        event="assigned",
        summary="音频",
        segment_count=1,
        duration_ms=800,
        occurred_at=NOW,
        created_at=NOW,
    )
    second = voice.claim_delivery(
        session,
        event_key="evt-1",
        rule_id=rule_id,
        device_id="dev-a",
        door_no=3,
        cert_no="c1",
        event="assigned",
        summary="音频",
        segment_count=1,
        duration_ms=800,
        occurred_at=NOW,
        created_at=NOW,
    )
    assert first is True
    assert second is False

    voice.finish_delivery(session, "evt-1", NOW, "ok", "done", NOW, True)
    listed = voice.list_deliveries(session, 10)
    assert listed[0]["status"] == "ok"
    assert listed[0]["downloaded"] is True


def test_missing_entities_return_not_found(session: Session) -> None:
    assert voice.get_asset(session, "nope") is None
    assert voice.rename_asset(session, "nope", "x") is False
    assert voice.delete_asset(session, "nope") is False
    assert voice.get_rule(session, 999) is None
    assert (
        voice.update_rule(
            session,
            999,
            devices=[],
            certs=[],
            events=[],
            time_windows=[],
            exempt=[],
            segments=[],
            volume=1,
            enabled=True,
            updated_at=NOW,
        )
        is False
    )
    assert voice.set_enabled(session, 999, True, NOW) is False
    voice.finish_delivery(session, "nope", NOW, "ok", "", NOW, False)


def test_claim_for_missing_device_keeps_its_key(session: Session) -> None:
    """设备不在册时投递行仍落（审计事实性不依赖主数据），以文本键可查。"""
    rule_id = voice.insert_rule(
        session,
        devices=["*"],
        certs=["*"],
        events=["assigned"],
        time_windows=[],
        exempt=[],
        segments=[],
        volume=8,
        enabled=True,
        created_at=NOW,
    )
    assert (
        voice.claim_delivery(
            session,
            event_key="evt-x",
            rule_id=rule_id,
            device_id="missing",
            door_no=1,
            cert_no="c",
            event="assigned",
            summary="",
            segment_count=0,
            duration_ms=0,
            occurred_at=NOW,
            created_at=NOW,
        )
        is True
    )
    assert voice.list_deliveries(session, 10)[0]["device_id"] == "missing"
