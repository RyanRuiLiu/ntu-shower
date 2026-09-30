"""访问控制：管理面静态令牌。

用户面操作接口匿名（鉴权由浴室系统凭证完成，见 docs/decisions/0034）；
未来的调用方身份体系（微信小程序 / 独立 APP）接入时再引入 provider 化的
令牌签发模块，届时一并回归本册标准。
"""

import secrets

from fastapi import Header, HTTPException

from .config import settings

# .env 模板中的占位值：沿用它们等同于没有鉴权，启动自检必须拒绝
PLACEHOLDER_TOKENS = frozenset({"change-me", ""})


class AdminTokenNotConfiguredError(RuntimeError):
    """运维令牌仍是 .env 模板里的占位值。

    依据 OWASP ASVS V2.1「禁止默认凭证」：宁可启动失败，也不允许
    一个管理面敞开在公网上的服务实例运行。
    """

    def __init__(self) -> None:
        super().__init__(
            "ADMIN_TOKEN 仍为占位值，服务拒绝启动。请在 server/.env 中设置真实令牌（模板见 server/.env.example）。"
        )


def assert_admin_token_configured() -> None:
    """启动自检：运维令牌仍为占位值时拒绝启动。"""
    if settings.admin_token in PLACEHOLDER_TOKENS:
        raise AdminTokenNotConfiguredError


def require_admin(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> None:
    """守卫：管理面接口必须携带运维静态令牌。

    比较使用 secrets.compare_digest（恒定时间）：逐字符短路比较会通过
    响应耗时泄漏令牌前缀，属时序侧信道（OWASP ASVS V2.2）。
    """
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="admin authentication required")
