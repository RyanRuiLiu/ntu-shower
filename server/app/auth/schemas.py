"""认证端点的对外契约。

`/api/v1` 下的 JSON 响应必须是具名模型（见 tests/test_api_contract.py），
所以这里给出形状，而不是在路由里直接拼字典。
"""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class AccountView(BaseModel):
    """会话建立后返回的账号视图；口令哈希绝不出现在这里。"""

    username: str
    capabilities: list[str]
    device_scope: list[str]


__all__: list[str] = ["AccountView", "LoginRequest"]
