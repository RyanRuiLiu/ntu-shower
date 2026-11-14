"""语音域：素材、规则、单元映射、投递记录。

`voice_units` 是唯一保留自然主键的表：一个语音单元名只对应一个素材，
它本身没有独立生命周期，加自增主键只会多一层无意义的连接。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UtcDateTime, utcnow


class VoiceAsset(Base):
    """一段已经规范化过的音频素材（mp3）。"""

    __tablename__ = "voice_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 素材标识，与磁盘文件名对应
    asset_id: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    #: 数字素材对应的数值；非数字素材为空
    digit: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class VoiceRule(Base):
    """一条播报规则：什么设备、什么事件、什么时段、播什么。"""

    __tablename__ = "voice_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    devices: Mapped[list[str]] = mapped_column(JSON)
    certs: Mapped[list[str]] = mapped_column(JSON)
    events: Mapped[list[str]] = mapped_column(JSON)
    time_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    exempt: Mapped[list[str]] = mapped_column(JSON, default=list)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    volume: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class VoiceUnit(Base):
    """语音单元 → 素材的映射，自然主键。"""

    __tablename__ = "voice_units"

    unit: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("voice_assets.asset_id", ondelete="RESTRICT"), index=True)


class VoiceDelivery(Base):
    """一次播报投递，保留 90 天。

    `event_key` 是幂等身份：重复派生或重复投递不会播第二遍。
    `status` 用字符串而不是枚举：它的取值集合属于 voice 模块的结果词汇，
    目前尚未收敛成闭集，硬编成枚举会让每次新增状态都要改迁移。
    """

    __tablename__ = "voice_deliveries"
    __table_args__ = (Index("ix_voice_deliveries_occurred_at", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("voice_rules.id", ondelete="RESTRICT"))
    device_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    door_no: Mapped[int] = mapped_column(Integer)
    cert_no: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(32))
    #: 投递摘要文本（_plan_summary 的产物），审计页直接展示；不是播放清单
    segments: Mapped[str] = mapped_column(Text, default="")
    segment_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime)
    dispatched_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(Text, default="")
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


__all__: list[str] = ["VoiceAsset", "VoiceDelivery", "VoiceRule", "VoiceUnit"]
