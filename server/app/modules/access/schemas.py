"""访问授权领域的 API 契约。

## 一个资源，一套模型

名单里的东西是**标识**（identity）——不论是微信给的 openid 还是我们签发的
网页标识，它都是"一个带能力的调用方身份"。因此只有一个记录模型
`IdentityRecord`，而不是按"谁在什么场景拿到它"分成四个子集模型。

此前有四个响应模型（登录结果、能力列表、签发结果、名单记录）彼此是字段子集
关系，同一个概念在四处各写一遍字段——改一处忘三处就漂移。

`kind` 用 `IdentityKind` 枚举而非裸字符串，让 OpenAPI 生成联合类型。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from .identity import IdentityKind


class WxLoginRequest(BaseModel):
    """小程序换取标识：wx.login 拿到的 code。"""

    code: str = Field(min_length=1, description="wx.login 返回的临时凭证")


class IdentityRequest(BaseModel):
    """登记一个标识。

    两种来源共用一个端点——它们创建的是同一种资源，只是 `identity` 的由来不同：
    微信给的 openid 需要登记，网页标识则由我们签发。用 `kind` 区分，
    避免两个几乎相同的端点和两份几乎相同的请求模型。
    """

    kind: IdentityKind
    capabilities: list[str] = Field(min_length=1)
    device_scope: list[str] = Field(
        default=["*"],
        description='作用域：能操作哪些设备（device_id 列表）。["*"] 表示全部设备',
    )
    label: str = Field(default="", description="人读备注：这个标识是谁在用")

    openid: str = Field(
        default="",
        description="kind=openid 时必填：该用户的微信 openid。kind=web 时留空，由服务端签发",
    )


class IdentityRecord(TypedDict):
    """一条标识记录。

    同一个模型用于：小程序换取标识的响应、查询自身能力的响应、登记/
    签发后的响应、名单列表的条目。四处的字段本就该一致。
    """

    identity: str
    kind: IdentityKind
    capabilities: list[str]
    device_scope: list[str]
    label: str
    created_at: str
