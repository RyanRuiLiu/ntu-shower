"""使用域：一次占用（会话）与其中的每一段连续出水。

会话与段分成两张表：会话回答「这个人这次洗了多久」，段回答「中间开了几次水」。
合成一张表，这两个问题就压成了一件事。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, enum_column, utcnow
from .enums import AssignedSource, ObservedSpan, SegmentSource


class UsageSession(Base):
    """一次占用：人 + 门 + 起止。"""

    __tablename__ = "usage_sessions"
    __table_args__ = (
        Index(
            "ix_usage_sessions_device_id_door_no_assigned_at",
            "device_id",
            "door_no",
            "assigned_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"), index=True)
    door_no: Mapped[int] = mapped_column(Integer)
    card_no: Mapped[int] = mapped_column(Integer, default=0)
    cert_no: Mapped[str] = mapped_column(String(32), default="", index=True)
    #: 分配时刻（服务端轴）。`assigned_source = observed` 时它是**下界**，真实起点更早。
    assigned_at: Mapped[datetime] = mapped_column(UtcDateTime)
    assigned_source: Mapped[AssignedSource] = mapped_column(enum_column(AssignedSource, "assigned_source"))
    #: 观测连续性：只描述本项目看没看见，不描述发生过什么；**单向只增不减**——
    #: 断开一次之后，后面接上的连续观测补不回失去的那一段。
    observed_span: Mapped[ObservedSpan] = mapped_column(
        enum_column(ObservedSpan, "observed_span"), default=ObservedSpan.CONTINUOUS
    )
    #: 设备自己累计的出水秒数，与用段汇总出的 flow_s 是两套口径。
    #: **两者不等就说明中间有段没进表**（丢帧、离线、设备卡住）。
    device_flow_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 段之和与 `device_flow_s` 是否相等，结束时定稿。**有 `derived` 段时恒为假**——
    #: 那段的长度就是从设备那个值推出来的，等式必然成立，拿它自证是循环论证。
    segments_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    flow_s: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class UsageSegment(Base):
    """会话里的一段连续出水。"""

    __tablename__ = "usage_segments"
    __table_args__ = (
        Index(
            "ix_usage_segments_device_id_door_no_opened_at",
            "device_id",
            "door_no",
            "opened_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("usage_sessions.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    door_no: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 这一段的长度。**同名列在 `UsageSession` 上是另一个量**（那个是段之和）。
    flow_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 设备写下的两个读数原文。段长取「同一口钟两个读数之差」，没有原文就无法复核、
    #: 也无法事后重算。
    opened_raw: Mapped[str] = mapped_column(String(32), default="")
    closed_raw: Mapped[str] = mapped_column(String(32), default="")
    #: 段长的来源；未关阀时为空，关阀那一刻才定稿
    source: Mapped[SegmentSource | None] = mapped_column(enum_column(SegmentSource, "segment_source"), default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


__all__: list[str] = ["UsageSegment", "UsageSession"]
