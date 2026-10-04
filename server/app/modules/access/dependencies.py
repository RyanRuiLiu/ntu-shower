"""授权守卫（全库唯一的判定实现）。

调用方在 `X-Identity` 头里带上自己的标识，服务端查身份库得能力位与设备作用域。

## 守卫形态

- `optional_identity`：带标识就解析（不在名单则 401），不带即匿名。公开端点据此
  按调用方裁剪视图（如 /devices 的 actions 字段）。
- `require_identity`：要求携带在名单里的标识（操作端点与 require_admin 的基座）。
- `require_admin`：管理端点守卫（admin 能力位，作用域恒 *）。
- `enforce`：能力位 × 设备作用域的一体化判定。操作端点先取调用方再在函数体内
  调它（目标设备随请求而定，函数体内调意图最直接）。
- `require_capability`：只问能力位，专给**没有设备维度**的能力（`tasks`）。它用的仍是
  同一份能力位集合，所以那不是第二个判据，只是作用域这一维不参与。

失败各有明确的问题类型：没带标识（unauthenticated，仅要求标识的端点）、标识
不在名单（unknown-identity）、缺能力位或设备不在作用域（capability-denied）。

## 设备作用域

能力回答"能做什么"，作用域回答"对哪些设备做"，两者缺一不可。**当一件事没有设备维度时
（见 capabilities.DEVICE_SCOPED），作用域这一维不参与**，判定走 [require_capability]。
判定集中在 [device_allowed] 一处，除 [enforce] 与 [require_capability] 外任何代码不自行判定。
"""

from fastapi import Depends, Header

from app.core.problems import CAPABILITY_DENIED, UNAUTHENTICATED, UNKNOWN_IDENTITY, ApiProblemError

from . import service
from .capabilities import DEVICE_SCOPED, Capability
from .schemas import IdentityRecord

#: 调用方携带自身标识的请求头。
IDENTITY_HEADER = "X-Identity"


def optional_identity(x_identity: str | None = Header(default=None, alias=IDENTITY_HEADER)) -> IdentityRecord | None:
    """带标识就解析（不在名单则 401），不带就是匿名。用于按调用方裁剪视图的端点。"""
    if not x_identity:
        return None
    caller = service.resolve_identity(x_identity)
    if caller is None:
        raise ApiProblemError(UNKNOWN_IDENTITY)
    return caller


def require_identity(x_identity: str | None = Header(default=None, alias=IDENTITY_HEADER)) -> IdentityRecord:
    """要求携带一个在名单里的标识，不要求具体能力。操作端点与 require_admin 的基座。"""
    caller = optional_identity(x_identity)
    if caller is None:
        raise ApiProblemError(UNAUTHENTICATED)
    return caller


def device_allowed(caller: IdentityRecord, device_id: str) -> bool:
    """设备作用域判定（唯一实现）："*" 通配全部，否则须显式列出。"""
    scope = caller["device_scope"]
    return "*" in scope or device_id in scope


def enforce(caller: IdentityRecord, capability: Capability, device_id: str) -> None:
    """能力 + 设备作用域的一体化判定（唯一实现）。不通过即抛 capability-denied。"""
    if capability.value not in caller["capabilities"]:
        raise ApiProblemError(CAPABILITY_DENIED, f"缺少能力：{capability.value}")
    if not device_allowed(caller, device_id):
        raise ApiProblemError(CAPABILITY_DENIED, f"作用域不含设备：{device_id}")


def require_capability(caller: IdentityRecord, capability: Capability) -> None:
    """只按能力位判定，不涉及设备作用域——**专给没有设备维度的能力**。

    哪些能力没有设备维度由 [DEVICE_SCOPED] 登记。拿它去判有设备维度的能力等于绕开
    作用域，所以这里当场报错：误用者第一个请求就炸，而不是静默放开一个洞。

    "没有设备维度"不是"作用域为 `*`"：`*_students` 名单是 SERVER scope、不分设备，
    "对哪些设备做"这一问对它不成立，而不是它的答案是"全部"。
    """
    if capability in DEVICE_SCOPED:
        message = f"{capability.value} 有设备维度，判定必须经 enforce"
        raise ValueError(message)
    if capability.value not in caller["capabilities"]:
        raise ApiProblemError(CAPABILITY_DENIED, f"缺少能力：{capability.value}")


def require_admin(caller: IdentityRecord = Depends(require_identity)) -> IdentityRecord:
    """管理端点守卫：要求 admin 能力位。管理台操作不限定设备（作用域恒 *）。"""
    enforce(caller, Capability.ADMIN, "*")
    return caller
