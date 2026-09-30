"""身份标识的类型。

与 capability 分开：能力回答"能做什么"，标识类型回答"这个标识从哪来"。
靠它分辨名单里哪些是小程序用户、哪些是为网页签发的标识。
"""

from enum import Enum


class IdentityKind(str, Enum):
    """标识来源。

    用 `str, Enum` 而非 `StrEnum`：运行时是 Python 3.10，后者 3.11 才有。
    """

    OPENID = "openid"
    """微信小程序用户：标识即其 openid。"""

    WEB = "web"
    """网页用户：标识是我方签发的不透明串。"""
