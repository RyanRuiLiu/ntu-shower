"""设备域的表：档案、凭据、单台配置覆盖、日志、链路事件与拨钟状态。

「设备」是单一归属的领域，表按实体前缀 `device_` 统一（拨钟状态原
clock_shifts 同步更名 device_clock_shifts）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts import DeviceLogKind, LinkEvent
from app.db.base import Base, UtcDateTime, enum_column, utcnow


class Device(Base):
    """一台中控设备。"""

    __tablename__ = "devices"

    #: 主键即设备业务标识（Android ID 一类的稳定串，ADR-0008）：稳定唯一，故
    #: 不设代理键——全库「每表 id 主键 + <实体>_id 外键，同名同型同义」。
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    #: 是否已建立信任关系；只有可信设备能执行特权帧
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    app_version: Mapped[str] = mapped_column(String(32), default="")
    version_code: Mapped[int] = mapped_column(Integer, default=0)
    os_version: Mapped[str] = mapped_column(String(32), default="")
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None, index=True)
    last_ip: Mapped[str | None] = mapped_column(String(45), default=None)
    booted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 设备钟与服务端钟的偏差（秒），用于判断设备时间是否被改过
    clock_skew_s: Mapped[float] = mapped_column(default=0.0)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class DeviceCredential(Base):
    """设备接入凭证。

    只存哈希，不存明文：数据库泄露不等于设备被接管。
    轮换 = 新增一行并给旧的写 `revoked_at`，不做原地更新，保留审计轨迹。
    """

    __tablename__ = "device_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    #: SHA-256(token)；token 本身是 32 字节随机串，熵足够，不需要慢哈希
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)


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


class DeviceClockShift(Base):
    """设备时钟被改动的记录与分配归属的平移结果。"""

    __tablename__ = "device_clock_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), unique=True)
    shifted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    restarted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    assignments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class DeviceLog(Base):
    """设备上送的日志，保留 30 天。"""

    __tablename__ = "device_logs"
    __table_args__ = (Index("ix_device_logs_device_id_kind_uploaded_at", "device_id", "kind", "uploaded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[DeviceLogKind] = mapped_column(enum_column(DeviceLogKind, "device_log_kind"))
    content: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class DeviceLinkEvent(Base):
    """设备上下线事件，保留 90 天。"""

    __tablename__ = "device_link_events"
    __table_args__ = (Index("ix_device_link_events_device_id_occurred_at", "device_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    event: Mapped[LinkEvent] = mapped_column(enum_column(LinkEvent, "link_event"))
    reason: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
