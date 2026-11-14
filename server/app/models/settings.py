"""状态域：设备配置、全局配置、时钟平移。

这三张都是「每个键只有一行」的状态表，写入用 UPSERT，不做追加。
`app_settings` 是单行表：id 恒为 1，用主键保证唯一，比「取最新一行」可靠。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, utcnow


class DeviceConfig(Base):
    """下发给单台设备的配置副本，`revision` 用于设备端判断是否需要重载。"""

    __tablename__ = "device_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), unique=True)
    #: 设备文本业务键留底：覆盖的历史不因设备除档而失联
    revision: Mapped[int] = mapped_column(Integer)
    #: **存的是与默认值的差异，空对象即全部沿用默认**。存完整配置会固化当时的默认值，
    #: 新增键永远下不下去。生效值每次现算：默认 ← 全局 ← 设备。
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class AppSettings(Base):
    """全局配置，单行。"""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class ClockShift(Base):
    """设备时钟被改动的记录与分配归属的平移结果。"""

    __tablename__ = "clock_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), unique=True)
    shifted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    restarted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    assignments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


__all__: list[str] = ["AppSettings", "ClockShift", "DeviceConfig"]
