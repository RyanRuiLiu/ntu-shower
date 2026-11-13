"""设备接入凭据。

签发时返回一次明文，库里只留摘要；吊销是写 `revoked_at` 而不是删行，
保留「谁在什么时候被吊销」的轨迹。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..core.tokens import generate_token, hash_token
from ..db.base import utcnow
from ..models.identity import Device, DeviceCredential


def _device_pk(session: Session, device_id: str) -> int | None:
    return session.scalar(select(Device.id).where(Device.device_id == device_id))


def issue(
    session: Session,
    device_id: str,
    label: str = "",
    expires_at: datetime | None = None,
) -> str | None:
    """签发一枚令牌，返回明文（只返回这一次）。设备不存在时返回 None。"""
    device_pk = _device_pk(session, device_id)
    if device_pk is None:
        return None
    token = generate_token()
    session.add(
        DeviceCredential(
            device_id=device_pk,
            token_hash=hash_token(token),
            label=label,
            expires_at=expires_at,
        )
    )
    session.commit()
    return token


def list_for_device(session: Session, device_id: str) -> list[dict[str, object]]:
    """列出某设备的凭据元数据；明文不存库，所以这里没有 token 字段。"""
    device_pk = _device_pk(session, device_id)
    if device_pk is None:
        return []
    rows = session.scalars(
        select(DeviceCredential)
        .where(DeviceCredential.device_id == device_pk, DeviceCredential.revoked_at.is_(None))
        .order_by(DeviceCredential.id)
    ).all()
    return [
        {
            "id": int(row.id),
            "label": row.label,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
        }
        for row in rows
    ]


def revoke_all(session: Session, device_id: str, now: datetime | None = None) -> int:
    """吊销该设备全部有效凭据，返回吊销条数。轮换前先调用它。"""
    device_pk = _device_pk(session, device_id)
    if device_pk is None:
        return 0
    stamp = now or utcnow()
    active_ids = session.scalars(
        select(DeviceCredential.id).where(
            DeviceCredential.device_id == device_pk,
            DeviceCredential.revoked_at.is_(None),
        )
    ).all()
    if active_ids:
        session.execute(update(DeviceCredential).where(DeviceCredential.id.in_(active_ids)).values(revoked_at=stamp))
        session.commit()
    return len(active_ids)


def revoke(session: Session, token: str, now: datetime | None = None) -> bool:
    """按令牌吊销（运维手里只有明文，没有行 id）。"""
    row = session.scalars(
        select(DeviceCredential).where(DeviceCredential.token_hash == hash_token(token))
    ).one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or utcnow()
    session.commit()
    return True


def authenticate_device(session: Session, token: str, now: datetime | None = None) -> str | None:
    """令牌有效则返回设备业务标识，否则 None。

    校验三件事：摘要存在、未吊销、未过期。过期比较用同一时钟源，避免设备与服务端
    时间轴混用。
    """
    moment = now or utcnow()
    row = session.execute(
        select(DeviceCredential, Device.device_id)
        .join(Device, DeviceCredential.device_id == Device.id)
        .where(DeviceCredential.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    credential, business_id = row
    if credential.revoked_at is not None:
        return None
    if credential.expires_at is not None and credential.expires_at <= moment:
        return None
    return str(business_id)


__all__: list[str] = ["authenticate_device", "issue", "list_for_device", "revoke", "revoke_all"]
