"""授权服务：登录换取身份、解析请求标识、管理员签发网页标识。"""

import logging
import secrets

from app.core.wechat import WeChatNotConfiguredError, code_to_openid

from . import repository
from .identity import IdentityKind
from .schemas import GrantedIdentity, GrantRecord, IssuedWebIdentity

logger = logging.getLogger("ntu-shower.access")

#: 网页标识的随机字节数。32 字节 = 256 位，不可枚举。
WEB_IDENTITY_BYTES = 32


def login_with_wx_code(code: str) -> GrantedIdentity | None:
    """小程序登录：用 wx.login 的 code 换取身份。

    返回 openid 本身作为标识，**不另发令牌**：openid 由微信签发、稳定不变，
    再包一层只是徒增过期与重登的处理。客户端存下它，之后每次请求带上。

    未授权用户返回 None——"换不到 openid"与"不在名单里"对外不区分，
    以免泄漏谁在名单中。
    """
    try:
        openid = code_to_openid(code)
    except WeChatNotConfiguredError:
        logger.warning("wx login attempted but appid/secret not configured")
        return None
    if openid is None:
        return None

    granted = repository.find_grant(openid)
    if granted is None:
        logger.info("login rejected, not granted: openid=%s", _mask_identity(openid))
        return None
    return {"identity": openid, "capabilities": granted["capabilities"]}


def resolve_identity(identity: str) -> GrantRecord | None:
    """解析请求携带的标识，返回其授权信息；未授权返回 None。"""
    return repository.find_grant(identity)


def issue_web_identity(label: str, capabilities: list[str]) -> IssuedWebIdentity:
    """为网页端签发一个标识并登记授权。

    网页拿不到 openid，因此标识本身就是它的身份：登记后与小程序用户出现在
    同一份名单里，管理员一眼能看到"这个标识是给谁用的"。
    """
    identity = secrets.token_urlsafe(WEB_IDENTITY_BYTES)
    repository.put_grant(identity, IdentityKind.WEB, capabilities, label)
    logger.info("web identity issued: label=%s capabilities=%s", label, ",".join(capabilities))
    return {"identity": identity, "label": label, "capabilities": capabilities}


def authorize_openid(openid: str, capabilities: list[str], label: str = "") -> GrantedIdentity:
    """登记一个小程序用户的授权（管理员操作）。

    返回该身份**当前**的能力集，形状与登录一致：这正是管理员想确认的事——
    "他现在能用这些了"。名单视图（含入库时间）走 list_grants。
    """
    repository.put_grant(openid, IdentityKind.OPENID, capabilities, label)
    logger.info("openid authorized: openid=%s capabilities=%s", _mask_identity(openid), ",".join(capabilities))
    return {"identity": openid, "capabilities": capabilities}


def revoke_identity(identity: str) -> bool:
    """撤销授权；该标识立即失去全部能力。"""
    removed = repository.delete_grant(identity)
    if removed:
        logger.info("identity revoked: identity=%s", _mask_identity(identity))
    return removed


def list_grants() -> list[GrantRecord]:
    return repository.list_grants()


def _mask_identity(identity: str) -> str:
    """日志不写全量标识：它是可直接用于冒充的凭据。"""
    if len(identity) <= 8:
        return "***"
    return f"{identity[:4]}...{identity[-4:]}"
