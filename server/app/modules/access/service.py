"""授权服务：换取标识、登记标识、解析请求标识。

## 一个概念一套函数

名单里的东西是**标识**（identity），两种来源（微信 openid / 我们签发）只是
`kind` 不同。因此登记只有一个入口 `put_identity`，而不是
`authorize_openid` 与 `issue_web_identity` 两个几乎相同的函数。

所有返回标识的函数都返回同一个 `IdentityRecord`，形状与名单一致。
"""

import logging
import secrets

from app.core.audit import mask_actor
from app.core.wechat import WeChatNotConfiguredError, code_to_openid

from . import repository
from .identity import IdentityKind
from .schemas import IdentityRecord

logger = logging.getLogger("ntu-shower.access")

#: 网页标识的随机字节数。32 字节 = 256 位，不可枚举。
WEB_IDENTITY_BYTES = 32


def put_identity(
    kind: IdentityKind,
    capabilities: list[str],
    label: str = "",
    openid: str = "",
) -> IdentityRecord:
    """登记一个标识并授予能力。

    - `kind=OPENID`：用微信给的 `openid` 作标识（登记，随后该用户可登录）
    - `kind=WEB`：服务端签发一个不可枚举的标识（网页拿不到 openid，
      标识本身就是它的身份）

    两种来源写进同一张表、出现在同一份名单，一眼能看到"这个标识是谁在用"。
    """
    identity = openid if kind is IdentityKind.OPENID else secrets.token_urlsafe(WEB_IDENTITY_BYTES)
    record = repository.put_identity(identity, kind, capabilities, label)
    logger.info(
        "identity put: kind=%s identity=%s capabilities=%s",
        kind.value,
        mask_actor(identity),
        ",".join(capabilities),
    )
    return record


def login_with_wx_code(code: str) -> IdentityRecord | None:
    """小程序登录：用 wx.login 的 code 换取身份。

    返回 openid 本身作为标识，**不另发令牌**：openid 由微信签发、稳定不变，
    再包一层只是徒增过期与重登的处理。客户端存下它，之后每次请求带上。

    未授权返回 None——"换不到 openid"与"不在名单里"对外不区分，
    以免泄漏谁在名单中。
    """
    try:
        openid = code_to_openid(code)
    except WeChatNotConfiguredError:
        logger.warning("wx login attempted but appid/secret not configured")
        return None
    if openid is None:
        return None

    record = repository.find_identity(openid)
    if record is None:
        logger.info("login rejected, not granted: openid=%s", mask_actor(openid))
    return record


def resolve_identity(identity: str) -> IdentityRecord | None:
    """解析请求携带的标识，返回其记录；未授权返回 None。"""
    return repository.find_identity(identity)


def revoke_identity(identity: str) -> bool:
    """撤销授权；该标识立即失去全部能力。"""
    removed = repository.delete_identity(identity)
    if removed:
        logger.info("identity revoked: identity=%s", mask_actor(identity))
    return removed


def list_identities() -> list[IdentityRecord]:
    """名单。"""
    return repository.list_identities()
