"""认证授权域的对外契约。

`/api/v1` 下的 JSON 响应必须是具名模型（见 tests/test_api_contract.py），这里给出
形状而不是在路由里直接拼字典。凭据明文只出现在 KeyIssued.token——签发瞬间的
唯一一次，之后任何响应都不再携带。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.contracts import Caller


class LoginRequest(BaseModel):
    username: str
    password: str


class AccountView(BaseModel):
    """会话建立后返回的账号视图；口令哈希绝不出现在这里。"""

    username: str
    name: str


class MeView(BaseModel):
    """调用方自身视图：我是谁、我有什么权限（任意已认证调用方）。"""

    username: str
    name: str
    scopes: list[str]
    device_scope: list[str]
    key_name: str | None


class UserCreate(BaseModel):
    """登记一个用户：username 唯一；不给口令即纯密钥调用方（machine user）。"""

    username: str = Field(min_length=2, max_length=64, pattern=r"^\S+$")
    name: str = Field(default="", max_length=64)
    password: str | None = Field(default=None, min_length=8, description="可选；给了即可登录管理台")


class UserUpdate(BaseModel):
    """编辑用户：只改传了的字段。password 用于设置/重置口令。"""

    name: str | None = Field(default=None, max_length=64)
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class UserView(BaseModel):
    id: int
    username: str
    name: str
    disabled: bool
    has_password: bool
    last_login_at: str
    created_at: str


class ScopeOption(BaseModel):
    """一个权限的对外描述：稳定 scope + 人读名称（字典派生，表单的唯一来源）。"""

    scope: str
    label: str


class KeyCreate(BaseModel):
    name: str = Field(default="", max_length=64)
    scopes: list[str] = Field(min_length=1)
    device_scope: list[str] = Field(
        default=["*"],
        description='设备作用域：能操作哪些设备（device_id 列表）。["*"] 表示全部设备',
    )


class KeyUpdate(BaseModel):
    """编辑密钥：只改传了的字段，令牌不变；已吊销的密钥拒绝编辑。"""

    name: str | None = Field(default=None, max_length=64)
    scopes: list[str] | None = None
    device_scope: list[str] | None = None


class KeyView(BaseModel):
    id: int
    user_id: int
    name: str
    scopes: list[str]
    device_scope: list[str]
    token_hint: str
    """摘要前 12 位：与审计 actor 字段同值，密钥↔审计行按它对账。"""
    last_used_at: str
    created_at: str
    revoked_at: str


class KeyIssued(BaseModel):
    """签发响应：记录 + 明文（只此一次）。"""

    id: int
    user_id: int
    name: str
    scopes: list[str]
    device_scope: list[str]
    token_hint: str
    last_used_at: str
    created_at: str
    revoked_at: str
    token: str


class UserCatalog(BaseModel):
    """`GET /users` 的读模型：权限字典 + 用户名单，用户页一次装载。"""

    scopes: list[ScopeOption]
    users: list[UserView]


class UserKeys(BaseModel):
    """`GET /users/{username}/keys` 的读模型：该用户的全部密钥（含已吊销）。"""

    keys: list[KeyView]


__all__: list[str] = [
    "AccountView",
    "Caller",
    "KeyCreate",
    "KeyIssued",
    "KeyUpdate",
    "KeyView",
    "LoginRequest",
    "MeView",
    "ScopeOption",
    "UserCatalog",
    "UserCreate",
    "UserKeys",
    "UserUpdate",
    "UserView",
]
