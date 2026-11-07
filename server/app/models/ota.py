"""OTA 域：发布版本与安装记录。

发布表用 `version` 作为业务唯一键，主键仍是自增整数；安装记录用复合主键，
因为它的身份就是「哪个版本装在哪台设备上」。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, utcnow


class OtaRelease(Base):
    """一次 APK 发布。"""

    __tablename__ = "ota_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    version_code: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 目标选择：`all` 或具体设备白名单
    target: Mapped[str] = mapped_column(String(32), default="all")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class OtaReleaseInstall(Base):
    """某台设备已经应用了某个版本。"""

    __tablename__ = "ota_release_installs"

    release_id: Mapped[int] = mapped_column(ForeignKey("ota_releases.id", ondelete="CASCADE"), primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UtcDateTime)


__all__: list[str] = ["OtaRelease", "OtaReleaseInstall"]
