"""门域五张表：现态、状态迁移流水、原始帧、使用会话与出水段。

`door_snapshots` 是原始层（设备说什么记什么，保留 30 天）；`door_states`、
`door_state_events`、`door_sessions`、`door_segments` 是从它派生的长期历史。
派生可从原始层重算的前提是原始层还在——原始层清了，派生层就是唯一事实。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts import AssignedSource, ObservedSpan, SegmentSource
from app.contracts import DoorState as DoorStateEnum
from app.db.base import Base, UtcDateTime, enum_column, utcnow


class DoorState(Base):
    """每个门一行的现态，写入走 UPSERT。

    表名用 `door_states` 而不是 `door_live`：单数、且 `live` 是过程描述，
    状态表要表达的是「当前状态」这件事本身。
    """

    __tablename__ = "door_states"
    __table_args__ = (UniqueConstraint("device_id", "door_no", name="uq_door_states_device_id_door_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    door_no: Mapped[int] = mapped_column(Integer)
    state: Mapped[DoorStateEnum] = mapped_column(enum_column(DoorStateEnum, "door_state"))
    card_no: Mapped[int] = mapped_column(Integer, default=0)
    cert_no: Mapped[str] = mapped_column(String(32), default="")
    acc_no: Mapped[str] = mapped_column(String(32), default="")
    open_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 当前这一段的开阀时刻。等待开阀时为空；**暂停后仍有值**——设备在关阀那一刻清空了
    #: `inTime`，这一段的开阀时刻改由 `door_segments` 回答。不要拿它判「此刻在不在出水」，
    #: 那是 `state` 的事。
    in_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    close_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 设备累计的出水秒数。是时长不是时刻；**不单调**——只在关阀时累加、释放时归零。
    bath_time_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 设备上报的原始状态整数，保留是为了排障时能对照协议。**别拿它判状态**：新增状态时
    #: 直接判整数会静默走错分支，判定一律用派生出的 `state`。
    status_raw: Mapped[int] = mapped_column(Integer, default=0)
    reported_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class DoorStateEvent(Base):
    """观测到的状态迁移，只追加。

    盲区帧与全貌帧不编造迁移——只有真正看到边沿才写一行。
    """

    __tablename__ = "door_state_events"
    __table_args__ = (
        # 按门查时间线是主要查询路径
        Index(
            "ix_door_state_events_device_id_door_no_occurred_at",
            "device_id",
            "door_no",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    door_no: Mapped[int] = mapped_column(Integer)
    from_state: Mapped[DoorStateEnum] = mapped_column(enum_column(DoorStateEnum, "door_state_from"))
    to_state: Mapped[DoorStateEnum] = mapped_column(enum_column(DoorStateEnum, "door_state_to"))
    card_no: Mapped[int] = mapped_column(Integer, default=0)
    cert_no: Mapped[str] = mapped_column(String(32), default="")
    bath_time_s: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class DoorSnapshot(Base):
    """设备上报的原始帧，保留 30 天。

    清理任务按 `received_at` 删除，所以它必须有独立索引；复合索引的第一列不是它，
    用不上。
    """

    __tablename__ = "door_snapshots"
    __table_args__ = (UniqueConstraint("device_id", "snapshot_id", name="uq_door_snapshots_device_id_snapshot_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    #: 设备为这一帧生成的标识，用于幂等去重
    snapshot_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int | None] = mapped_column(Integer, default=None)
    #: 帧来源原话。原始层不解释：协议外的取值也照记（回执语义要求原话落库），
    #: 派生层判定用的词汇表是 modules/doors/service.py 的 SOURCE_* 常量。
    source: Mapped[str] = mapped_column(String(16), default="")
    captured_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    #: 帧内全部门的状态原文，不解释
    doors: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    #: 设备自报的时钟字符串，原文保留
    device_clock: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class DoorSession(Base):
    """一次占用：人 + 门 + 起止（服务端轴）。"""

    __tablename__ = "door_sessions"
    __table_args__ = (
        Index(
            "ix_door_sessions_device_id_door_no_assigned_at",
            "device_id",
            "door_no",
            "assigned_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
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
    #: 各段之和，加上在途那截（advance 每帧更新）。与 `device_flow_s` 是两套口径：
    #: **两者不等就说明中间有段没进表**（丢帧、离线、设备卡住）。
    total_flow_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 设备自己累计的出水秒数（暂停时累加、释放时归零，由 MAX 收敛）。
    device_flow_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 段之和与 `device_flow_s` 是否相等，结束时定稿。**有 derived 段时恒为假**——
    #: 那段的长度就是从设备那个值推出来的，等式必然成立，拿它自证是循环论证。
    segments_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class DoorSegment(Base):
    """会话里的一段连续出水。"""

    __tablename__ = "door_segments"
    __table_args__ = (
        Index(
            "ix_door_segments_device_id_door_no_opened_at",
            "device_id",
            "door_no",
            "opened_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("door_sessions.id", ondelete="CASCADE"), index=True)
    #: 反规范化冗余（可经 session_id JOIN door_sessions 得出）：段处理是每帧热路径，
    #: 按设备直查在途段依赖本表的复合索引，省去逐帧 JOIN。会话生命周期内设备与
    #: 喷头不可变（换人 = 关旧会话开新会话），冗余值无漂移风险。
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    door_no: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 这一段的长度。**同名列在 `DoorSession` 上是另一个量**（那个是各段之和）。
    flow_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 设备写下的两个读数原文。段长取「同一口钟两个读数之差」，没有原文就无法复核、
    #: 也无法事后重算。
    opened_raw: Mapped[str] = mapped_column(String(32), default="")
    closed_raw: Mapped[str] = mapped_column(String(32), default="")
    #: 段长的来源；未关阀时空，关阀那一刻才定稿
    source: Mapped[SegmentSource | None] = mapped_column(enum_column(SegmentSource, "segment_source"), default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


__all__: list[str] = ["DoorSegment", "DoorSession", "DoorSnapshot", "DoorState", "DoorStateEvent"]
