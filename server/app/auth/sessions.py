"""管理台会话：签名 Cookie。

用 Flask 风格的无状态签名会话：载荷里只放用户名、过期时刻与随机串，
签名用 HMAC-SHA256，密钥来自环境变量。不放权限位——每次请求都从账号表读，
禁用账号与改能力位立即生效，不需要等会话过期。

无状态会话无法单个吊销，所以有效期设短（默认 12 小时），并且服务端每次都查
`disabled`。要即时吊销用禁用账号，而不是试图让签名失效。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Literal, TypedDict

from app.core.config import settings

COOKIE_NAME = "ntu_session"
_VERSION = "v1"

#: 进程内临时密钥：只在没配 session_secret 时生成，生命周期与进程一致。
_PROCESS_SECRET = secrets.token_bytes(32)


def _secret() -> bytes:
    configured = settings.session_secret
    return configured.encode("utf-8") if configured else _PROCESS_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(username: str, ttl_seconds: int | None = None) -> str:
    """签发一枚会话令牌。"""
    ttl = settings.session_ttl_seconds if ttl_seconds is None else ttl_seconds
    payload = {
        "u": username,
        "e": int(time.time()) + ttl,
        "n": secrets.token_urlsafe(8),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{_VERSION}.{body}.{signature}"


def verify(token: str | None) -> str | None:
    """校验并返回用户名；签名不对、格式不对、已过期都返回 None。"""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        return None
    _version, body, signature = parts
    expected = _b64(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    expires = payload.get("e")
    username = payload.get("u")
    if not isinstance(expires, int) or not isinstance(username, str) or not username:
        return None
    if expires <= int(time.time()):
        return None
    return username


class CookieOptions(TypedDict):
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]
    max_age: int
    path: str


def cookie_options(secure: bool) -> CookieOptions:
    """统一的 Cookie 属性：HttpOnly 挡脚本读取，SameSite=Lax 挡跨站携带。"""
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "max_age": settings.session_ttl_seconds,
        "path": "/",
    }


__all__: list[str] = ["COOKIE_NAME", "CookieOptions", "cookie_options", "issue", "verify"]
