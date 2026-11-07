"""授权服务：登记标识、解析请求标识、吊销。

名单里的东西是标识（identity），两种来源（微信 openid / 我方签发的网页标识）只是
`kind` 不同，登记只有一个入口 `put_identity`。所有返回标识的函数都返回同一个
`IdentityRecord`，形状与名单一致。

本项目不含任何微信交互（code 换 openid 在 status-panel）；这里的 `kind=openid`
只是名单里的来源标记，openid 的值由管理员在身份页手工登记。
"""

import secrets

from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.persistence import principals as principals_store

from .identity import IdentityKind
from .schemas import IdentityRecord

#: 网页标识的随机字节数。32 字节 = 256 位，不可枚举。
WEB_IDENTITY_BYTES = 32


def put_identity(
    session: Session,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    identity: str = "",
    device_scope: list[str] | None = None,
) -> IdentityRecord:
    """登记一个标识并授予能力与设备作用域。

    - `kind=OPENID`：`identity` 即小程序用户的 openid（status-panel 转发请求时就带它）
    - `kind=WEB`：`identity` 为空由服务端签发不可枚举的标识（网页拿不到 openid，
      标识即身份）；给了值就是自创标识，原样采用

    两种来源写进同一张表、出现在同一份名单。编辑既有标识走 [update_identity]，
    不走这里：登记时 web 标识必须新生成或显式自创，编辑时必须沿用原标识。
    """
    if kind is IdentityKind.OPENID and not identity:
        message = "openid identity requires a value"
        raise ValueError(message)
    if not identity:
        identity = secrets.token_urlsafe(WEB_IDENTITY_BYTES)
    record = principals_store.put(session, identity, kind, capabilities, label, device_scope)
    log_event(
        "INFO",
        "access.identity-put",
        "身份已登记",
        kind=kind.value,
        capabilities=",".join(capabilities),
    )
    return record


def update_identity(
    session: Session,
    identity: str,
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    device_scope: list[str] | None = None,
) -> IdentityRecord:
    """编辑既有标识的能力位、作用域与备注；标识本身不变，下一次请求即生效。"""
    record = principals_store.put(session, identity, kind, capabilities, label, device_scope)
    log_event(
        "INFO",
        "access.identity-put",
        "身份已登记",
        kind=kind.value,
        capabilities=",".join(capabilities),
    )
    return record


def resolve_identity(session: Session, identity: str) -> IdentityRecord | None:
    """解析请求携带的标识，返回其记录；未授权返回 None。"""
    return principals_store.find(session, identity)


def revoke_identity(session: Session, identity: str) -> bool:
    """撤销授权；该标识立即失去全部能力。"""
    removed = principals_store.delete(session, identity)
    if removed:
        log_event("INFO", "access.identity-revoked", "身份已吊销")
    return removed


def list_identities(session: Session) -> list[IdentityRecord]:
    """名单。"""
    return principals_store.list_all(session)
