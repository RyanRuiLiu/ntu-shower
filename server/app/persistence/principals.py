"""主体（principal）的持久化与认证材料校验。

合并了原先 `access`（名单）与 `users`（账号）两套几乎同形的实现：
一个主体可以同时拥有口令、API 凭据，或者只是授权名单里的一条 openid 记录。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.passwords import hash_password, needs_rehash, verify_password
from ..auth.tokens import generate_token, hash_token
from ..core.clock import format_rfc3339
from ..db.base import utcnow
from ..models.enums import IdentityKind
from ..models.identity import Principal
from ..modules.access.schemas import IdentityRecord

DEFAULT_SCOPE: list[str] = ["*"]


def _view(row: Principal) -> IdentityRecord:
    return {
        "identity": row.name,
        "kind": row.kind,
        "capabilities": list(row.capabilities),
        "device_scope": list(row.device_scope),
        "label": row.label,
        "created_at": format_rfc3339(row.created_at),
    }


def _account_view(row: Principal) -> dict[str, Any]:
    """给认证端点用的账号视图；口令与凭据哈希都不出这一层。"""
    return {
        "username": row.name,
        "capabilities": list(row.capabilities),
        "device_scope": list(row.device_scope),
        "disabled": bool(row.disabled),
        "last_seen_at": row.last_seen_at,
    }


def put(
    session: Session,
    name: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> IdentityRecord:
    """登记或更新一个主体。全量覆盖能力集、作用域与备注；`kind` 保持不变。"""
    scope = device_scope if device_scope is not None else list(DEFAULT_SCOPE)
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None:
        row = Principal(
            name=name,
            kind=kind,
            capabilities=list(capabilities),
            device_scope=scope,
            label=label,
        )
        session.add(row)
    else:
        row.capabilities = list(capabilities)
        row.device_scope = scope
        row.label = label
    session.commit()
    return _view(row)


def find(session: Session, name: str) -> IdentityRecord | None:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    return _view(row) if row is not None else None


def list_all(session: Session) -> list[IdentityRecord]:
    rows = session.scalars(select(Principal).order_by(Principal.created_at.desc(), Principal.id.desc())).all()
    return [_view(row) for row in rows]


def delete(session: Session, name: str) -> bool:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


# ── 口令认证 ──


def create_account(
    session: Session,
    name: str,
    password: str,
    capabilities: list[str],
    device_scope: list[str] | None = None,
) -> dict[str, Any]:
    row = Principal(
        name=name,
        kind=IdentityKind.WEB,
        password_hash=hash_password(password),
        capabilities=list(capabilities),
        device_scope=list(device_scope) if device_scope is not None else list(DEFAULT_SCOPE),
    )
    session.add(row)
    session.commit()
    return _account_view(row)


def find_account(session: Session, name: str) -> dict[str, Any] | None:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    return _account_view(row) if row is not None else None


def authenticate(session: Session, name: str, password: str, now: datetime | None = None) -> dict[str, Any] | None:
    """校验口令。账号不存在与口令错误返回同样的 None，避免账号枚举。"""
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None or row.disabled or row.password_hash is None:
        return None
    if not verify_password(row.password_hash, password):
        return None
    if needs_rehash(row.password_hash):
        row.password_hash = hash_password(password)
    row.last_seen_at = now or utcnow()
    session.commit()
    return _account_view(row)


def set_password(session: Session, name: str, password: str) -> bool:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None:
        return False
    row.password_hash = hash_password(password)
    session.commit()
    return True


def set_disabled(session: Session, name: str, disabled: bool) -> bool:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None:
        return False
    row.disabled = disabled
    session.commit()
    return True


# ── API 凭据（Authorization: Bearer）──


def issue_token(session: Session, name: str) -> str | None:
    """给主体签发一枚 API 凭据，返回明文（只此一次）；主体不存在返回 None。"""
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None:
        return None
    token = generate_token()
    row.token_hash = hash_token(token)
    session.commit()
    return token


def resolve_token(session: Session, token: str) -> str | None:
    """凭据 → 主体名。禁用主体的凭据立即失效。"""
    row = session.scalars(select(Principal).where(Principal.token_hash == hash_token(token))).one_or_none()
    if row is None or row.disabled:
        return None
    return row.name


def revoke_token(session: Session, name: str) -> bool:
    row = session.scalars(select(Principal).where(Principal.name == name)).one_or_none()
    if row is None or row.token_hash is None:
        return False
    row.token_hash = None
    session.commit()
    return True


__all__: list[str] = [
    "DEFAULT_SCOPE",
    "authenticate",
    "create_account",
    "delete",
    "find",
    "find_account",
    "issue_token",
    "list_all",
    "put",
    "resolve_token",
    "revoke_token",
    "set_disabled",
    "set_password",
]
