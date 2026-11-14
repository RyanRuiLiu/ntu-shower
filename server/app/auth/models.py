"""用户与 API 密钥的表模型（认证授权域自有，归属本域目录）。

模型对齐 GitHub / Stripe 的「一个身份、多枚密钥」：用户是人还是机器不做区分
（GitHub 官方的 machine user 同型），口令与密钥只是同一身份下的不同凭据类型。

凭据只存摘要：SHA-256(API key)。库被读走不等于密钥被接管——这是摘要而非慢哈希
的理由：密钥本身是 32 字节随机串（见 core.tokens），没有可字典化的结构。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, UtcDateTime, utcnow


class User(Base):
    """一个用户：管理台操作者、小程序用户、外部系统共用一张表。

    - `hashed_password`：有口令即可登管理台（会话 Cookie 通道）；没有即纯密钥
      调用方（GitHub machine user 同型），不设 kind 之类的类型列——登录方式由
      凭据决定，不由身份决定
    - `name`：人读显示名（GitHub/GitLab 的 username/name 两列模型）
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 业务唯一键：管理台是用户名，小程序调用方是 openid，外部系统是登记名
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    #: Argon2id 编码串（含算法、参数与盐）；没有口令的用户为空
    hashed_password: Mapped[str | None] = mapped_column(String(255), default=None)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    keys: Mapped[list[ApiKey]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    """一枚 API 密钥：权限集与设备作用域都附在密钥上，不附在用户上。

    GitHub fine-grained PAT 同型：权限与资源范围是每枚密钥的属性。轮换 = 发一枚
    新的、切过去、吊销旧的——多枚共存天然提供重叠窗口，无需宽限期机制。
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped[User] = relationship(back_populates="keys")
    name: Mapped[str] = mapped_column(String(64), default="")
    #: SHA-256(完整密钥串，含前缀)；明文只在签发响应出现一次
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: 权限集（auth.permissions 字典的子集，可含 "*"）
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 设备作用域：["*"] 或设备 id 白名单
    device_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)


__all__: list[str] = ["ApiKey", "User"]
