"""令牌与凭据的生成、摘要与提取（纯加密工具，无业务语义）。

服务对象是设备接入令牌与主体 API 凭据。住在 core：persistence 存取凭据材料
必须用到它们，放 auth 层就是持久化反向依赖认证层。


令牌本身是 32 字节随机串，熵足够，所以库里只存 SHA-256 摘要——不需要慢哈希。
用摘要而不是明文的意义在于：数据库被读走，不等于设备被接管。
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def generate_token() -> str:
    """生成一个新令牌（URL 安全字符，可直接放进 Authorization 头）。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """令牌摘要。查询与比对一律用摘要，不比对明文。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_bearer(header: str | None) -> str | None:
    """从 `Authorization: Bearer <token>` 中取出令牌；格式不符返回 None。"""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


__all__: list[str] = ["TOKEN_BYTES", "extract_bearer", "generate_token", "hash_token"]
