"""访问授权领域的 API 契约。

四个响应模型按"谁在什么场景下拿到它"区分，而不是按字段多少：

- `GrantedIdentity` —— 小程序登录后拿到自己的身份与能力；
- `CapabilityList` —— 已持有身份者查询自己有哪些能力；
- `GrantRecord` —— 管理员在名单里看到的一条授权；
- `IssuedWebIdentity` —— 管理员新签发的网页标识（**唯一一次**能看到标识本身的机会）。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class LoginRequest(BaseModel):
    """小程序登录：wx.login 拿到的 code。"""

    code: str = Field(min_length=1, description="wx.login 返回的临时凭证")


class GrantRequest(BaseModel):
    """管理员登记一个小程序用户的授权。"""

    openid: str = Field(min_length=8, description="该用户的微信 openid")
    capabilities: list[str] = Field(min_length=1)
    label: str = Field(default="", description="人读备注（是谁）")


class WebIdentityRequest(BaseModel):
    """管理员为网页端签发标识。"""

    label: str = Field(min_length=1, description="人读备注（给谁用的）")
    capabilities: list[str] = Field(min_length=1)


class GrantedIdentity(TypedDict):
    """登录结果：客户端自行保存 identity，此后每次请求带上。"""

    identity: str
    capabilities: list[str]


class CapabilityList(TypedDict):
    """当前标识的全部能力（非"本次校验的那一个"）。"""

    capabilities: list[str]


class IssuedWebIdentity(TypedDict):
    """新签发的网页标识。

    `identity` 只在签发这一次的响应里出现——名单接口不再回显它，因此这一步
    是把它交付给使用者的唯一机会。
    """

    identity: str
    label: str
    capabilities: list[str]


class GrantRecord(TypedDict):
    """名单中的一条授权。"""

    identity: str
    kind: str
    capabilities: list[str]
    label: str
    created_at: str
