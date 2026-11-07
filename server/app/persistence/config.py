"""配置的持久化：全局单行与逐设备覆盖。

只负责读写与版本号落库。校验、取值裁剪、合并规则属于配置语义，留在
`modules/config` 里，不搬进来——持久化层不该知道「什么算合法配置」。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.identity import Device
from ..models.settings import AppSettings, DeviceConfig

GLOBAL_ROW_ID = 1


def get_global(session: Session) -> tuple[int, dict[str, Any]]:
    row = session.get(AppSettings, GLOBAL_ROW_ID)
    if row is None:
        return (0, {})
    return (int(row.revision), dict(row.config))


def put_global(session: Session, revision: int, values: dict[str, Any]) -> None:
    row = session.get(AppSettings, GLOBAL_ROW_ID)
    if row is None:
        session.add(AppSettings(id=GLOBAL_ROW_ID, revision=revision, config=values))
    else:
        row.revision = revision
        row.config = values
    session.commit()


def get_device(session: Session, device_id: str) -> tuple[int, dict[str, Any]] | None:
    device_pk = session.scalar(select(Device.id).where(Device.device_id == device_id))
    if device_pk is None:
        return None
    row = session.scalars(select(DeviceConfig).where(DeviceConfig.device_id == device_pk)).one_or_none()
    if row is None:
        return None
    return (int(row.revision), dict(row.config))


def put_device(session: Session, device_id: str, revision: int, values: dict[str, Any]) -> None:
    device_pk = session.scalar(select(Device.id).where(Device.device_id == device_id))
    if device_pk is None:
        return
    row = session.scalars(select(DeviceConfig).where(DeviceConfig.device_id == device_pk)).one_or_none()
    if row is None:
        session.add(DeviceConfig(device_id=device_pk, revision=revision, config=values))
    else:
        row.revision = revision
        row.config = values
    session.commit()


__all__: list[str] = ["GLOBAL_ROW_ID", "get_device", "get_global", "put_device", "put_global"]
