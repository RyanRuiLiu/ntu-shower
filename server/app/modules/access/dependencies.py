"""授权守卫。

调用方在 `X-Identity` 头里带上自己的标识（小程序的 openid 或网页标识），
服务端查名单得到能力。查看类接口不需要这个头。

用法：
    @router.post("/x", dependencies=[Depends(require_capability(Capability.OPS))])
    def x(caller: IdentityRecord = Depends(require_identity)) -> ...

三种情况各有明确的问题类型，客户端可据 type 分支：没带标识（unauthenticated）、
标识不在名单里（unknown-identity）、在名单里但缺所需能力或设备不在作用域
（capability-denied）。

## 设备作用域

能力回答"能做什么"，作用域回答"对哪些设备做"——两者缺一不可：授予 ops 却限定
只有 28-2 的身份，对 28-3 的分配必须被拒。判定集中在 [device_allowed] 一处，
所有业务路由经 require_capability(capability, device_id) 间接使用，不出现
第二份判定逻辑。
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


def device_allowed(caller: IdentityRecord, device_id: str) -> bool:
    """设备作用域判定（唯一实现）："*" 通配全部，否则须显式列出。"""
    scope = caller["device_scope"]
    return "*" in scope or device_id in scope


def enforce(caller: IdentityRecord, capability: Capability, device_id: str) -> None:
    """能力 + 设备作用域的一体化判定（唯一实现）。不通过即抛 capability-denied。

    为什么是普通函数而不是 FastAPI 依赖：device_id 在请求体里，依赖无法在
    函数定义时取到它。路由处理函数体内调用，意图一目了然。
    """
    if capability.value not in caller["capabilities"]:
        raise ApiProblemError(CAPABILITY_DENIED, f"缺少能力：{capability.value}")
    if not device_allowed(caller, device_id):
        raise ApiProblemError(CAPABILITY_DENIED, f"作用域不含设备：{device_id}")


def require_capability(capability: Capability, device_id: str) -> Callable[..., IdentityRecord]:
    """构造守卫：要求调用方持有该能力，且目标设备在其作用域内。"""

    def guard(
        caller: IdentityRecord = Depends(require_identity),
        device_id: str = device_id,
    ) -> IdentityRecord:
        enforce(caller, capability, device_id)
        return caller

    return guard
