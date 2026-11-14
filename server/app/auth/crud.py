"""用户与 API 密钥的存取层：认证材料的唯一读写入口。

明文凭据只在这一层生成、只在签发瞬间返回一次，库里永远是 SHA-256 摘要。解析
（resolve_key）按摘要查库；禁用用户与已吊销密钥即时失效——判定不缓存，与守卫
「每请求查库」的约定一致（OWASP：撤销必须当场生效）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..contracts import KeyRecord, UserRecord
from ..core.clock import format_rfc3339
from ..core.passwords import hash_password, needs_rehash, verify_password
from ..core.tokens import generate_token, hash_token
from ..db.base import utcnow
from .models import ApiKey, User

#: 密钥前缀：完整密钥 = 前缀 + 43 字符 URL-safe 随机串。GitHub 官方的
#: github_pat_ 与 GitLab 的 glpat_ 同型——任何场景下看一眼就知道这把钥属于
#: 哪个系统，日志与代码检索时不会误伤。
KEY_PREFIX = "ntushower_"


def user_view(row: User) -> UserRecord:
    return {
        "id": row.id,
        "username": row.username,
        "name": row.name,
        "disabled": bool(row.disabled),
        "has_password": row.hashed_password is not None,
        "last_login_at": format_rfc3339(row.last_login_at) if row.last_login_at else "",
        "created_at": format_rfc3339(row.created_at),
    }


def key_view(row: ApiKey) -> KeyRecord:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "scopes": list(row.scopes),
        "device_scope": list(row.device_scope),
        "token_hint": row.token_hash[:12],
        "last_used_at": format_rfc3339(row.last_used_at) if row.last_used_at else "",
        "created_at": format_rfc3339(row.created_at),
        "revoked_at": format_rfc3339(row.revoked_at) if row.revoked_at else "",
    }


# ── 用户 ──


def find_row(session: Session, username: str) -> User | None:
    return session.scalars(select(User).where(User.username == username)).one_or_none()


def find_user(session: Session, username: str) -> UserRecord | None:
    row = find_row(session, username)
    return user_view(row) if row is not None else None


def create_user(session: Session, username: str, name: str = "", password: str | None = None) -> UserRecord:
    row = User(username=username, name=name, hashed_password=hash_password(password) if password else None)
    session.add(row)
    session.commit()
    return user_view(row)


def list_users(session: Session) -> list[UserRecord]:
    rows = session.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())).all()
    return [user_view(row) for row in rows]


def update_user(
    session: Session,
    username: str,
    name: str | None = None,
    disabled: bool | None = None,
    password: str | None = None,
) -> UserRecord | None:
    """编辑用户：只改传了的字段。禁用即时生效——守卫每请求查库，无缓存可绕。"""
    row = find_row(session, username)
    if row is None:
        return None
    if name is not None:
        row.name = name
    if disabled is not None:
        row.disabled = disabled
    if password is not None:
        row.hashed_password = hash_password(password)
    session.commit()
    return user_view(row)


def delete_user(session: Session, username: str) -> bool:
    """删除用户（名下密钥随 ORM 关系一并删除）。不存在返回 False。"""
    row = find_row(session, username)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def authenticate(session: Session, username: str, password: str, now: datetime | None = None) -> UserRecord | None:
    """校验口令。用户不存在与口令错误返回同一个 None，避免账号枚举。"""
    row = find_row(session, username)
    if row is None or row.disabled or row.hashed_password is None:
        return None
    if not verify_password(row.hashed_password, password):
        return None
    if needs_rehash(row.hashed_password):
        row.hashed_password = hash_password(password)
    row.last_login_at = now or utcnow()
    session.commit()
    return user_view(row)


# ── API 密钥 ──


def issue_key(
    session: Session, username: str, name: str, scopes: list[str], device_scope: list[str]
) -> tuple[KeyRecord, str] | None:
    """签发一枚密钥，返回（记录, 明文）。用户不存在返回 None。

    默认永不过期（GitHub 官方允许 infinite lifetime；Stripe 密钥默认长期）：
    生命周期只有「签发 → 吊销」两态，长期不换是设计内的正常状态。
    """
    row = find_row(session, username)
    if row is None:
        return None
    token = KEY_PREFIX + generate_token()
    key = ApiKey(
        user_id=row.id,
        name=name,
        token_hash=hash_token(token),
        scopes=list(scopes),
        device_scope=list(device_scope),
    )
    session.add(key)
    session.commit()
    return key_view(key), token


def list_keys(session: Session, username: str) -> list[KeyRecord]:
    """某用户的全部密钥（含已吊销，吊销历史本身是要看的事实）。"""
    rows = session.scalars(
        select(ApiKey).join(User).where(User.username == username).order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
    ).all()
    return [key_view(row) for row in rows]


def revoke_key(session: Session, username: str, key_id: int) -> bool:
    """吊销单枚密钥：下一次请求即 401，同用户其他密钥不受影响。"""
    row = session.scalars(select(ApiKey).join(User).where(User.username == username, ApiKey.id == key_id)).one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    session.commit()
    return True


def resolve_key(session: Session, token: str) -> tuple[UserRecord, KeyRecord] | None:
    """密钥明文 →（用户视图, 密钥视图）。无效、已吊销或用户被禁用返回 None。"""
    row = session.scalars(select(ApiKey).join(User).where(ApiKey.token_hash == hash_token(token))).one_or_none()
    if row is None or row.revoked_at is not None or row.user.disabled:
        return None
    row.last_used_at = utcnow()
    session.commit()
    return user_view(row.user), key_view(row)


__all__: list[str] = [
    "KEY_PREFIX",
    "authenticate",
    "create_user",
    "delete_user",
    "find_row",
    "find_user",
    "issue_key",
    "key_view",
    "list_keys",
    "list_users",
    "resolve_key",
    "revoke_key",
    "update_user",
    "user_view",
]
