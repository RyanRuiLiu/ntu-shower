"""授权守卫。

调用方在 `X-Identity` 头里带上自己的标识（小程序的 openid 或网页标识），
服务端查名单得到能力。查看类接口不需要这个头。

用法：
    @router.post("/x", dependencies=[Depends(require_capability(Capability.OPS))])
    def x(caller: IdentityRecord = Depends(require_identity)) -> ...

两种情况各有明确的问题类型，客户端可据 type 分支：没带标识（unauthenticated）、
标识不在名单里（unknown-identity）、在名单里但缺所需能力（capability-denied）。
"""

from collections.abc import Callable

from fastapi import Depends, Header

from app.core.problems import CAPABILITY_DENIED, UNAUTHENTICATED, UNKNOWN_IDENTITY, ApiProblemError

from . import service
from .capabilities import Capability
from .schemas import IdentityRecord

#: 调用方携带自身标识的请求头。
IDENTITY_HEADER = "X-Identity"


def require_identity(x_identity: str | None = Header(default=None, alias=IDENTITY_HEADER)) -> IdentityRecord:
    """要求携带一个在名单里的标识，不要求具体能力。

    用于"查询自己有什么能力"这类接口——若它本身要求某种能力，只被授予另一种
    能力的用户就永远问不出自己的权限。
    """
    if not x_identity:
        raise ApiProblemError(UNAUTHENTICATED)
    caller = service.resolve_identity(x_identity)
    if caller is None:
        raise ApiProblemError(UNKNOWN_IDENTITY)
    return caller


def require_capability(capability: Capability) -> Callable[..., IdentityRecord]:
    """构造守卫：要求调用方持有该能力。"""

    def guard(caller: IdentityRecord = Depends(require_identity)) -> IdentityRecord:
        if capability.value not in caller["capabilities"]:
            raise ApiProblemError(CAPABILITY_DENIED, f"缺少能力：{capability.value}")
        return caller

    return guard
