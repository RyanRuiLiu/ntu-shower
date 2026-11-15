"""配置的持久化：全局单行与逐设备覆盖。

只负责读写与版本号落库。校验、取值裁剪、合并规则属于配置语义，留在
`modules/config` 里，不搬进来——持久化层不该知道「什么算合法配置」。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.modules.config.models import AppSettings
from app.modules.devices.models import Device

GLOBAL_ROW_ID = 1
#: 库里还没有这一行时（全新安装、尚未写过任何覆盖）对外呈现的修订号：
#: GET 报 1，第一次写入变成 2。
FRESH_REVISION = 1


def get_global(session: Session) -> tuple[int, dict[str, Any]]:
    row = session.get(AppSettings, GLOBAL_ROW_ID)
    if row is None:
        return (FRESH_REVISION, {})
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
    """设备的覆盖差异；从未覆盖过（修订号 0）时给 None——两者是同一件事。"""
    row = session.get(Device, device_id)
    if row is None or row.config_revision == 0:
        return None
    return (int(row.config_revision), dict(row.config_override or {}))


def put_device(session: Session, device_id: str, revision: int, values: dict[str, Any]) -> None:
    row = session.get(Device, device_id)
    if row is None:
        return
    row.config_revision = revision
    row.config_override = values
    session.commit()


__all__: list[str] = ["GLOBAL_ROW_ID", "get_device", "get_global", "put_device", "put_global"]
