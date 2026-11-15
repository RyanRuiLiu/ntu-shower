"""拨钟状态：记在设备影子行上，`clock_shifted_at` 非空即「设备钟还偏着」。

- 覆盖式写入，一台设备同时只可能偏着一次
- 列表按 `clock_shifted_at` 升序（拨得最早的先到期）
- 调回成功就清空，不保留「已调回」状态
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import format_rfc3339
from app.modules.devices.models import Device


def record(session: Session, device_id: str, shifted_at: datetime, assignments: list[dict[str, Any]]) -> None:
    device = session.get(Device, device_id)
    if device is None:
        return
    device.clock_shifted_at = shifted_at
    device.clock_shift_assignments = assignments
    session.commit()


def get(session: Session, device_id: str) -> dict[str, Any] | None:
    device = session.get(Device, device_id)
    if device is None or device.clock_shifted_at is None:
        return None
    return as_view(device)


def list_shifted(session: Session) -> list[dict[str, Any]]:
    """全部还偏着的设备，按拨钟时刻升序。"""
    rows = session.scalars(
        select(Device).where(Device.clock_shifted_at.is_not(None)).order_by(Device.clock_shifted_at)
    ).all()
    return [as_view(row) for row in rows]


def clear(session: Session, device_id: str) -> None:
    device = session.get(Device, device_id)
    if device is not None and device.clock_shifted_at is not None:
        device.clock_shifted_at = None
        device.clock_shift_assignments = None
        session.commit()


def as_view(device: Device) -> dict[str, Any]:
    """统一的读出形状：设备标识是业务字符串，时刻是 RFC 3339，归属是原样列表。"""
    return {
        "device_id": device.id,
        "shifted_at": format_rfc3339(device.clock_shifted_at) if device.clock_shifted_at else "",
        "assignments": list(device.clock_shift_assignments or []),
    }


__all__: list[str] = ["as_view", "clear", "get", "list_shifted", "record"]
