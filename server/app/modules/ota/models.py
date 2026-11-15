"""OTA 域：发布版本与安装记录。

发布表用 `version` 作为业务唯一键，主键仍是自增整数；安装记录用复合主键，
因为它的身份就是「哪个版本装在哪台设备上」。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UtcDateTime


class OtaRelease(TimestampMixin, Base):
    """一次 APK 发布。"""

    __tablename__ = "ota_releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    version_code: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    #: 调试发布：无视版本号推送，**但已安装确认过即不再推**。
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 目标设备列表（JSON）：`["*"]` 表示全部设备，通配词汇与 voice_rules 一致。
    target: Mapped[list[str]] = mapped_column(JSON, default=list)


class OtaReleaseInstall(Base):
    """某台设备已经应用了某个版本。

    时间豁免：`applied_at` 即创建时刻（事件行），无 created_at。
    """

    __tablename__ = "ota_release_installs"

    release_id: Mapped[int] = mapped_column(ForeignKey("ota_releases.id", ondelete="CASCADE"), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"), primary_key=True, index=True)
    applied_at: Mapped[datetime] = mapped_column(UtcDateTime)


__all__: list[str] = ["OtaRelease", "OtaReleaseInstall"]
