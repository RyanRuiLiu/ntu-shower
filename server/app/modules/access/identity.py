"""身份标识的类型。

与 capability 分开：能力回答"能做什么"，标识类型回答"这个标识从哪来"。
靠它分辨名单里哪些是小程序用户、哪些是为网页签发的标识。
"""

from enum import StrEnum


class IdentityKind(StrEnum):
    """标识来源。

    `StrEnum`（3.11+）：成员即字符串值，入库与 OpenAPI 都按取值消费。
    """

    OPENID = "openid"
    """微信小程序用户：标识即其 openid。"""

    WEB = "web"
    """网页用户：标识是我方签发的不透明串。"""
