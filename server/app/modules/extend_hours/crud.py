"""拨钟状态：一行一台设备，有行即「设备钟还偏着」。

契约与旧实现一致：
- 覆盖式写入，一台设备同时只可能偏着一次
- 列表按 `shifted_at` 升序（拨得最早的先到期）
- 调回成功就删行，不保留「已调回」状态
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import format_rfc3339
from app.modules.devices.models import Device
from app.modules.devices.models import DeviceClockShift as ClockShift


def _device(session: Session, device_id: str) -> Device | None:
    return session.scalars(select(Device).where(Device.id == device_id)).one_or_none()


def record(
    session: Session,
    device_id: str,
    shifted_at: datetime,
    assignments: list[dict[str, Any]],
) -> None:
    device = _device(session, device_id)
    if device is None:
        return
    row = session.scalars(select(ClockShift).where(ClockShift.device_id == device.id)).one_or_none()
    if row is None:
        session.add(ClockShift(device_id=device.id, shifted_at=shifted_at, assignments=assignments))
    else:
        row.shifted_at = shifted_at
        row.assignments = assignments
    session.commit()


def get(session: Session, device_id: str) -> dict[str, Any] | None:
    device = _device(session, device_id)
    if device is None:
        return None
    row = session.scalars(select(ClockShift).where(ClockShift.device_id == device.id)).one_or_none()
    return as_view(row, device.id) if row is not None else None


def list_shifted(session: Session) -> list[dict[str, Any]]:
    """全部还偏着的设备，按拨钟时刻升序。"""
    rows = session.execute(
        select(ClockShift, Device.id).join(Device, ClockShift.device_id == Device.id).order_by(ClockShift.shifted_at)
    ).all()
    return [as_view(row, business_id) for row, business_id in rows]


def clear(session: Session, device_id: str) -> None:
    device = _device(session, device_id)
    if device is None:
        return
    row = session.scalars(select(ClockShift).where(ClockShift.device_id == device.id)).one_or_none()
    if row is not None:
        session.delete(row)
        session.commit()


def as_view(row: ClockShift, business_id: str) -> dict[str, Any]:
    """统一的读出形状：设备标识是业务字符串，时刻是 RFC 3339，归属是原样列表。"""
    return {
        "device_id": business_id,
        "shifted_at": format_rfc3339(row.shifted_at),
        "restarted_at": format_rfc3339(row.restarted_at) if row.restarted_at else "",
        "assignments": list(row.assignments),
    }


__all__: list[str] = ["as_view", "clear", "get", "list_shifted", "record"]
