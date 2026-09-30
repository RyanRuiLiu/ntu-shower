"""访问控制：管理面静态令牌。

用户面操作接口匿名（鉴权由浴室系统凭证完成，见 docs/decisions/0034）；
未来的调用方身份体系（微信小程序 / 独立 APP）接入时再引入 provider 化的
令牌签发模块，届时一并回归本册标准。
"""

from fastapi import Header, HTTPException

from .config import settings


def require_admin(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> None:
    """守卫：管理面接口必须携带运维静态令牌。"""
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="admin authentication required")
