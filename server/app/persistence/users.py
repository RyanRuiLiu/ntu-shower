"""管理台账号。

对外只返回账号的可展示字段，`password_hash` 不出这一层——一旦泄漏到路由或日志，
哈希就变成了可以被离线爆破的素材。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.passwords import hash_password, needs_rehash, verify_password
from ..db.base import utcnow
from ..models.identity import AppUser


def _view(row: AppUser) -> dict[str, Any]:
    return {
        "username": row.username,
        "capabilities": list(row.capabilities),
        "device_scope": list(row.device_scope),
        "disabled": bool(row.disabled),
        "last_login_at": row.last_login_at,
    }


def create_user(
    session: Session,
    username: str,
    password: str,
    capabilities: list[str],
    device_scope: list[str] | None = None,
) -> dict[str, Any]:
    row = AppUser(
        username=username,
        password_hash=hash_password(password),
        capabilities=list(capabilities),
        device_scope=list(device_scope) if device_scope is not None else ["*"],
    )
    session.add(row)
    session.commit()
    return _view(row)


def find_user(session: Session, username: str) -> dict[str, Any] | None:
    row = session.scalars(select(AppUser).where(AppUser.username == username)).one_or_none()
    return _view(row) if row is not None else None


def authenticate(session: Session, username: str, password: str, now: datetime | None = None) -> dict[str, Any] | None:
    """校验账号口令，成功则记一次认证时刻。

    口令错误与账号不存在返回同样的 None，调用方不应据此区分——否则接口就成了
    账号枚举器。
    """
    row = session.scalars(select(AppUser).where(AppUser.username == username)).one_or_none()
    if row is None or row.disabled:
        return None
    if not verify_password(row.password_hash, password):
        return None
    if needs_rehash(row.password_hash):
        row.password_hash = hash_password(password)
    row.last_login_at = now or utcnow()
    session.commit()
    return _view(row)


def set_password(session: Session, username: str, password: str) -> bool:
    row = session.scalars(select(AppUser).where(AppUser.username == username)).one_or_none()
    if row is None:
        return False
    row.password_hash = hash_password(password)
    session.commit()
    return True


def set_disabled(session: Session, username: str, disabled: bool) -> bool:
    row = session.scalars(select(AppUser).where(AppUser.username == username)).one_or_none()
    if row is None:
        return False
    row.disabled = disabled
    session.commit()
    return True


__all__: list[str] = ["authenticate", "create_user", "find_user", "set_disabled", "set_password"]
