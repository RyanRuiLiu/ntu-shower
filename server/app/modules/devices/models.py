"""设备域的表：影子行（档案+配置覆盖+电源计划+停用位）、凭据、日志与链路事件。

「设备」是单一归属的领域，表按实体前缀 `device_` 统一。`devices` 是设备影子
（AWS IoT Shadow 同型）：一行承载档案、配置覆盖、上报事实（开关机计划与时区）、
hold 状态与停用位——低频的一行一设备状态不另立表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts import DeviceLogKind, HoldSource, LinkEvent
from app.db.base import Base, CreatedAtMixin, TimestampMixin, UtcDateTime, enum_column


class Device(TimestampMixin, Base):
    """一台中控设备的影子行。"""

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
    # ── 上报事实（device.status 帧）：设备从浴室 App 参数文件读到的平台开关机计划 ──
    # 列名镜像 V8 平台的 autoCloseTime / autoOpenTime（与 doors 域镜像 openTime 同一先例）。
    auto_close_time: Mapped[str | None] = mapped_column(String(5), default=None)
    auto_open_time: Mapped[str | None] = mapped_column(String(5), default=None)
    #: 设备默认时区偏移（分钟；UTC+8 = 480）。浴室 App 判时刻用的是本机时区。
    zone_offset_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    # ── 出水与占用阈值（device.status 帧，分钟）：同一份参数文件里的三根线 ──
    # 各楼配置不同（2026-11-23 体检：六楼占用上限 30 分钟、其余 20 分钟），段长上限、
    # 盲区划断与续期阈值按设备取值；缺报回退 contracts 的默认。
    max_batch_time_min: Mapped[int | None] = mapped_column(Integer, default=None)
    max_in_space_time_min: Mapped[int | None] = mapped_column(Integer, default=None)
    max_batch_space_time_min: Mapped[int | None] = mapped_column(Integer, default=None)
    facts_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    # ── hold 状态：非空即「今晚在 hold 中」（恒温器 Hold Until 语义，一次性）──
    hold_started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    hold_source: Mapped[HoldSource | None] = mapped_column(enum_column(HoldSource, "hold_source"), default=None)
    #: 已施加的钟面偏移（秒；NULL = 意图已建、尚未施加）。施加后墙钟 = 真实 + 偏移。
    hold_offset_s: Mapped[int | None] = mapped_column(Integer, default=None)
    #: 施加瞬间的名单占用快照与重分配标记 [{door_no, cert_no, done}]。重分配是一次性的。
    hold_assignments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    #: MCU 布防截止到点（真实轴）：布防写入即落库（截止 = 次日 auto_close_time，整夜
    #: 覆盖），正常在拨钟成功后与偏置一起就位；拨钟失败时也会非空——它同时也是
    #: 「`hold_offset_s` 为空但布防已在位」的唯一标记（取消意图不能只清状态）。
    arming_deadline: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    #: 单台配置覆盖。存的是与默认值的差异，空对象/修订号 0 即全部沿用默认。
    #: 生效值每次现算：默认 ← 全局 ← 设备。
    config_revision: Mapped[int] = mapped_column(Integer, default=0)
    config_override: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    #: 停用位：非空即已除档。停用设备拒绝握手与自注册，历史数据全部保留。
    retired_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class DeviceCredential(CreatedAtMixin, Base):
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
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)


class DeviceLog(CreatedAtMixin, Base):
    """设备上送的日志，每个（设备，kind）只留最近 `LOG_KEEP` 份（见 devices.crud）。"""

    __tablename__ = "device_logs"
    __table_args__ = (Index("ix_device_logs_device_id_kind_uploaded_at", "device_id", "kind", "uploaded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[DeviceLogKind] = mapped_column(enum_column(DeviceLogKind, "device_log_kind"))
    content: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(UtcDateTime)


class DeviceLinkEvent(CreatedAtMixin, Base):
    """设备上下线事件，保留期随 audit_retention_days（统一单一旋钮）。"""

    __tablename__ = "device_link_events"
    __table_args__ = (Index("ix_device_link_events_device_id_occurred_at", "device_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    event: Mapped[LinkEvent] = mapped_column(enum_column(LinkEvent, "link_event"))
    reason: Mapped[str] = mapped_column(String(128), default="")
