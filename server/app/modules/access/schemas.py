"""访问授权领域的 API 契约。

名单里的东西是标识（identity）：不论微信 openid 还是我方签发的网页标识，都是
"一个带能力的调用方身份"。只有一个记录模型 IdentityRecord，登记、签发、名单、
查询自身共用同一形状；kind 用 IdentityKind 枚举让 OpenAPI 生成联合类型。
"""

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from .identity import IdentityKind


class IdentityRequest(BaseModel):
    """登记一个标识。

    两种来源共用一个端点：kind=openid 登记小程序用户（openid 必填），kind=web
    由服务端签发不可枚举的随机标识——也可经 identity 自创指定标识串（留空则
    生成）。它们创建的是同一种资源。
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
        description="kind=openid 时必填：该用户的微信 openid。kind=web 时留空",
    )
    identity: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        pattern=r"^\S+$",
        description="kind=web 时可选：自创的标识串。留空由服务端生成",
    )


class IdentityRecord(TypedDict):
    """一条标识记录，登记、签发、名单、查询自身四处共用同一形状。"""

    identity: str
    kind: IdentityKind
    capabilities: list[str]
    device_scope: list[str]
    label: str
    created_at: str
