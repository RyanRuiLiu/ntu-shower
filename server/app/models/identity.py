"""身份域：设备、设备凭证、访问标识、管理台账号。

设备与访问标识都改成「整数主键 + 业务唯一键」：原库把设备上报的字符串直接当主键，
改一个字段值就要级联整库；新库用整数主键，字符串只作为业务唯一键。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UtcDateTime, enum_column, utcnow
from ..models.enums import IdentityKind


class Principal(Base):
    """主体：管理台账号、小程序 openid、服务间调用凭据共用一张表。

    旧库里 `access_identity` 与新建的 `app_users` 字段几乎完全相同，是两份真相：
    同一个「能被授予能力位的主体」被拆成两处，认证与授权各读一半。这里合并。

    认证材料都是**可选且只存哈希**：
    - `password_hash`：管理台账号走口令认证（Argon2id）
    - `token_hash`：非浏览器调用方走 `Authorization: Bearer`，只比对摘要
    - 小程序 openid 由 status-panel 转发，主体在这里只作授权名单
    """

    __tablename__ = "principals"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 业务唯一键：管理台是用户名，小程序调用方是 openid
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[IdentityKind] = mapped_column(enum_column(IdentityKind, "identity_kind"))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 设备作用域。`["*"]` 表示全部设备；其余为设备字符串标识的白名单。
    device_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(64), default="")
    #: Argon2id 编码串（含算法、参数与盐）；没有口令的主体为空
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    #: SHA-256(API token)；没有签发凭据的主体为空
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


__all__: list[str] = ["Principal"]
