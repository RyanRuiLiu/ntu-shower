"""门域：现态、状态转换流水、原始帧。

三层的关系：`door_snapshots` 是原始层（设备说什么记什么），`door_states` 与
`door_state_events` 是从它推出来的。原始层保留 30 天只用于近期排障；派生层才是
长期历史。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

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
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"), index=True)
    door_no: Mapped[int] = mapped_column(Integer)
    state: Mapped[DoorStateEnum] = mapped_column(enum_column(DoorStateEnum, "door_state"))
    card_no: Mapped[int] = mapped_column(Integer, default=0)
    cert_no: Mapped[str] = mapped_column(String(32), default="")
    acc_no: Mapped[str] = mapped_column(String(32), default="")
    open_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 当前这一段的开阀时刻。等待开阀时为空；**暂停后仍有值**——设备在关阀那一刻清空了
    #: `inTime`，这一段的开阀时刻改由 `usage_segments` 回答。不要拿它判「此刻在不在出水」，
    #: 那是 `state` 的事。
    in_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    close_time: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 设备累计的出水秒数。是时长不是时刻；**不单调**——只在关阀时累加、释放时归零。
    bath_time_s: Mapped[int] = mapped_column(Integer, default=0)
    #: 设备上报的原始状态整数，保留是为了排障时能对照协议。**别拿它判状态**：新增状态时
    #: 直接判整数会静默走错分支，判定一律用派生出的 `state`。
    status_raw: Mapped[int] = mapped_column(Integer, default=0)
    reported_at: Mapped[datetime] = mapped_column(UtcDateTime)
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
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"), index=True)
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


__all__: list[str] = ["DoorSnapshot", "DoorState", "DoorStateEvent"]
