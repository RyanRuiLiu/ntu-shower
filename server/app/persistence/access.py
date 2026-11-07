"""标识名单的持久化。

契约与旧实现一致：登记是全量覆盖（能力集、作用域、备注一起提交），
`kind` 在更新时保持不变，`created_at` 保持首次登记的值。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.clock import format_rfc3339
from ..models.identity import AccessIdentity
from ..modules.access.identity import IdentityKind
from ..modules.access.schemas import IdentityRecord

DEFAULT_SCOPE: list[str] = ["*"]


def put_identity(
    session: Session,
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> IdentityRecord:
    """登记或更新一条标识。

    更新分支只覆盖能力集、作用域与备注：`kind` 表示标识从哪来，改它等于换一个身份，
    应当删了重建，而不是原地改。
    """
    scope = device_scope if device_scope is not None else list(DEFAULT_SCOPE)
    row = session.scalars(select(AccessIdentity).where(AccessIdentity.identity == identity)).one_or_none()
    if row is None:
        row = AccessIdentity(
            identity=identity,
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
    return to_record(row)


def find_identity(session: Session, identity: str) -> IdentityRecord | None:
    row = session.scalars(select(AccessIdentity).where(AccessIdentity.identity == identity)).one_or_none()
    return to_record(row) if row is not None else None


def list_identities(session: Session) -> list[IdentityRecord]:
    rows = session.scalars(
        select(AccessIdentity).order_by(AccessIdentity.created_at.desc(), AccessIdentity.id.desc())
    ).all()
    return [to_record(row) for row in rows]


def delete_identity(session: Session, identity: str) -> bool:
    row = session.scalars(select(AccessIdentity).where(AccessIdentity.identity == identity)).one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def to_record(row: AccessIdentity) -> IdentityRecord:
    """ORM 实体 → 域记录。时间统一按 RFC 3339 输出，与旧实现完全同形。"""
    record: IdentityRecord = {
        "identity": row.identity,
        "kind": row.kind,
        "capabilities": list(row.capabilities),
        "device_scope": list(row.device_scope),
        "label": row.label,
        "created_at": format_rfc3339(row.created_at),
    }
    return record


__all__: list[str] = ["delete_identity", "find_identity", "list_identities", "put_identity", "to_record"]
