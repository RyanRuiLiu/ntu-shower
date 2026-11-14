"""新库的 schema 契约。

这组用例守的是「模型定义与数据库实际结构一致」，不是业务行为：
- 表集合完整（漏一个模型，Alembic 会生成一条删表迁移）
- 每张表能真的建出来
- SQLite 的外键开关按连接打开
- 同一张表上的多个枚举列不会产生重名约束
- 数据库里的命令状态枚举与 `core.outcomes` 的取值集合完全一致
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from app.contracts import CommandStatus
from app.core import outcomes
from app.db.base import Base
from app.db.engine import create_db_engine

EXPECTED_TABLES: set[str] = {
    "api_calls",
    "app_settings",
    "job_runs",
    "device_clock_shifts",
    "command_audits",
    "device_configs",
    "device_credentials",
    "device_link_events",
    "device_logs",
    "devices",
    "door_snapshots",
    "door_state_events",
    "door_states",
    "api_keys",
    "users",
    "ota_release_installs",
    "ota_releases",
    "usage_segments",
    "usage_sessions",
    "voice_assets",
    "voice_deliveries",
    "voice_rules",
    "voice_units",
}


def _engine(tmp_path: Path) -> Engine:
    return create_db_engine(f"sqlite:///{tmp_path / 'schema.db'}")


def test_model_set_matches_expected() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_schema_can_be_created(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        names = {
            row[0]
            for row in conn.exec_driver_sql(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
            )
        }
    engine.dispose()
    assert names == EXPECTED_TABLES


def test_foreign_keys_pragma_is_enabled(tmp_path: Path) -> None:
    """SQLite 默认不强制外键，必须每条连接单独打开。"""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    engine.dispose()


def test_enum_checks_do_not_collide(tmp_path: Path) -> None:
    """同一张表上的两个枚举列必须各有一个独立 CHECK，重名会让 batch 迁移失败。"""
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        ddl = conn.exec_driver_sql("select sql from sqlite_master where name='door_state_events'").scalar_one()
    engine.dispose()
    assert "ck_door_state_events_door_state_from" in ddl
    assert "ck_door_state_events_door_state_to" in ddl


def test_command_status_matches_outcomes() -> None:
    """命令状态的数据库枚举与跨端契约同源，不允许各写一份。"""
    assert {member.value for member in CommandStatus} == set(outcomes.VALUES)


def test_database_roundtrip_keeps_timezone(tmp_path: Path) -> None:
    """写入再读出，时区必须还在。

    时间列用 `DateTime(timezone=True)`；SQLite 存的是字符串，如果解析回来变成 naive
    datetime，跨时区比较时会静默偏移——这类错误不会报错，只会让统计数字慢慢错。
    """
    from sqlalchemy import select

    from app.db.session import Database
    from app.modules.devices.models import Device

    db = Database(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    Base.metadata.create_all(db.engine)

    with db.new_session() as session:
        session.add(Device(id="dev-0001", capabilities=["session"]))
        session.commit()

    with db.new_session() as session:
        device = session.scalars(select(Device)).one()
        assert device.id == "dev-0001"
        assert device.capabilities == ["session"]
        assert device.created_at.tzinfo is not None
        assert device.created_at.utcoffset() is not None

    dependency = db.session()
    try:
        assert next(dependency).is_active
    finally:
        dependency.close()

    db.dispose()
