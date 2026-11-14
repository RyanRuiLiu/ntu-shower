"""状态域：设备配置、全局配置、时钟平移。

这三张都是「每个键只有一行」的状态表，写入用 UPSERT，不做追加。
`app_settings` 是单行表：id 恒为 1，用主键保证唯一，比「取最新一行」可靠。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UtcDateTime, utcnow


class AppSettings(Base):
    """全局配置，单行。"""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
