"""身份域：设备、设备凭证、访问标识、管理台账号。

设备与访问标识都改成「整数主键 + 业务唯一键」：原库把设备上报的字符串直接当主键，
改一个字段值就要级联整库；新库用整数主键，字符串只作为业务唯一键。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, enum_column, utcnow
from ..modules.access.identity import IdentityKind


class Device(Base):
    """一台中控设备。"""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 设备上报的原始标识（Android ID 一类的稳定串），业务唯一键
    device_id: Mapped[str] = mapped_column(String(64), unique=True)
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
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    #: SHA-256(token)；token 本身是 32 字节随机串，熵足够，不需要慢哈希
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)


class AccessIdentity(Base):
    """可授予能力位的身份：小程序用户（openid）与管理台签发标识。"""

    __tablename__ = "access_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[IdentityKind] = mapped_column(enum_column(IdentityKind, "identity_kind"))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 设备作用域。`["*"]` 表示全部设备；其余为设备字符串标识的白名单。
    #: 用 JSON 而不是关联表：它由 API 整体读写，不参与 SQL 连接查询。
    device_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class AppUser(Base):
    """管理台账号，替代原来「在浏览器里粘贴身份串」的做法。"""

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    #: Argon2id 编码串（含算法、参数、盐），由 argon2-cffi 生成
    password_hash: Mapped[str] = mapped_column(String(255))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    device_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


__all__: list[str] = ["AccessIdentity", "AppUser", "Device", "DeviceCredential"]
