"""保留期统一单一旋钮的钉子。

全部保留表跟随 settings.audit_retention_days（NIST SP 800-92 的 90 天热保留
主流口径）：登记完整性、业务永久表不误入、prune 按单一旋钮执行、VACUUM 只在
设备离线的静默窗口运行——四者由这里钉住。业务永久表（会话/段/门状态迁移）
的 docstring 写的是「永久保留」。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import retention
from app.core.config import settings
from app.core.connections import manager
from app.modules.devices.models import DeviceCredential, DeviceLinkEvent, DeviceLog
from app.modules.voice.models import VoiceDelivery
from app.persistence import api_calls
from app.persistence.audit import ApiCall, CommandAudit, JobRun

DECLARED_TABLES = {ApiCall, CommandAudit, JobRun, DeviceLinkEvent, VoiceDelivery}


@pytest.fixture
def session(tmp_path: Path) -> Session:
    from app.db.base import Base
    from app.db.session import Database

    database = Database(f"sqlite:///{tmp_path / 'retain.db'}")
    Base.metadata.create_all(database.engine)
    with database.new_session() as db_session:
        yield db_session
    database.dispose()


def test_every_retained_table_is_registered(session: Session) -> None:
    """声明了保留期的表都在 RETENTIONS 里，时刻列与表匹配。"""
    registered = {table for table, _column in retention.RETENTIONS}
    assert registered == DECLARED_TABLES
    for table, column in retention.RETENTIONS:
        assert column.key in {c.name for c in table.__table__.columns}


def test_permanent_tables_are_not_registered(session: Session) -> None:
    """永久保留的业务表不得进入清理登记（防止误删运营历史）。"""
    permanent = {DeviceCredential, DeviceLog}
    registered_tables = {table for table, _column in retention.RETENTIONS}
    assert registered_tables.isdisjoint(permanent)


def _seed_old_command(session: Session, days_ago: int, seq: int) -> None:
    session.add(
        CommandAudit(
            command_id=f"cmd-retain-{seq}",
            action="clock.set",
            created_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    session.commit()


def test_prune_follows_the_single_knob(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """保留期是单一旋钮：改 audit_retention_days 一处，全部表同时生效。

    以 command_audits 为钉：旋钮 30 时 60 天前的行被清，旋钮 90 时同一行保留。
    """
    _seed_old_command(session, days_ago=60, seq=1)

    monkeypatch.setattr(settings, "audit_retention_days", 30)
    assert api_calls.prune(session, 30, retention.RETENTIONS) == 1

    _seed_old_command(session, days_ago=60, seq=1)
    monkeypatch.setattr(settings, "audit_retention_days", 90)
    assert api_calls.prune(session, 90, retention.RETENTIONS) == 0
    remaining = session.scalars(select(CommandAudit.created_at)).all()
    assert len(remaining) == 1


def test_prune_uses_the_setting_value(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """prune 的天数参数与 settings.audit_retention_days 同源（单一旋钮的接线钉）。"""
    captured: dict[str, int] = {}

    def spy(_session: Session, retention_days: int, _retentions: object) -> int:
        captured["days"] = retention_days
        return 0

    monkeypatch.setattr(retention.api_calls, "prune", spy)
    asyncio.run(retention.prune_once())
    assert captured["days"] == settings.audit_retention_days


def test_vacuum_skips_while_a_device_is_online(monkeypatch: pytest.MonkeyPatch) -> None:
    """有设备在线（白天在用）时不维护：VACUUM 需要独占，不能撞业务写入。"""
    monkeypatch.setattr(
        manager,
        "online_connections",
        staticmethod(lambda: [("dev-x", object())]),
    )
    # 在线即跳过：sqlite3.connect 不会被触达
    monkeypatch.setattr(
        retention.sqlite3,
        "connect",
        lambda *_a: (_ for _ in ()).throw(AssertionError("不该连库")),
    )
    asyncio.run(retention.maybe_vacuum())


def test_vacuum_runs_in_the_silent_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """全设备离线的静默窗口里执行 VACUUM，并记录归还的页数。"""
    import sqlite3

    db = tmp_path / "vac.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(1000)])
    con.execute("DELETE FROM t")
    con.commit()
    con.close()

    # 客户端夹具的 lifespan 会让 maintenance 循环跑一轮（置 _last_vacuum）；
    # 复位模块状态，让本测试不受同一 worker 里先前用例的影响。
    monkeypatch.setattr(retention, "_last_vacuum", 0)
    monkeypatch.setattr(manager, "online_connections", staticmethod(lambda: []))
    real_connect = sqlite3.connect
    monkeypatch.setattr(retention.sqlite3, "connect", lambda *_a, **_kw: real_connect(db, isolation_level=None))
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        retention, "log_event", lambda _level, _event, _message, **kw: events.append(("maintenance.vacuum", kw))
    )

    asyncio.run(retention.maybe_vacuum())

    assert events, "VACUUM 未执行或未留痕"
    assert events[0][0] == "maintenance.vacuum"
