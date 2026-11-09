"""授权守卫（全库唯一的判定实现）。

调用方通过标准 `Authorization: Bearer` 凭据或签名会话 Cookie 表明身份，
服务端查主体表得能力位与设备作用域。

## 守卫形态

- `optional_identity`：带凭据就解析（无效则 401），不带即匿名。公开端点据此
  按调用方裁剪视图（如 /devices 的 actions 字段）。
- `require_identity`：要求一个可解析的身份（操作端点与 require_admin 的基座）。
- `require_admin`：管理端点守卫（admin 能力位，作用域恒 *）。
- `enforce`：能力位 × 设备作用域的一体化判定。操作端点先取调用方再在函数体内
  调它（目标设备随请求而定，函数体内调意图最直接）。
- `require_capability`：只问能力位，专给**没有设备维度**的能力（`tasks`）。它用的仍是
  同一份能力位集合，所以那不是第二个判据，只是作用域这一维不参与。

失败各有明确的问题类型：没带凭据（unauthenticated，仅要求身份的端点）、凭据
无效（unknown-identity）、缺能力位或设备不在作用域（capability-denied）。

## 设备作用域

能力回答"能做什么"，作用域回答"对哪些设备做"，两者缺一不可。**当一件事没有设备维度时
（见 capabilities.DEVICE_SCOPED），作用域这一维不参与**，判定走 [require_capability]。
判定集中在 [device_allowed] 一处，除 [enforce] 与 [require_capability] 外任何代码不自行判定。
"""

from typing import Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user
from app.auth.tokens import extract_bearer
from app.core.problems import CAPABILITY_DENIED, UNAUTHENTICATED, UNKNOWN_IDENTITY, ApiProblemError
from app.db.deps import get_db
from app.persistence import principals as principals_store

from ...models.enums import IdentityKind
from . import service
from .capabilities import DEVICE_SCOPED, Capability
from .schemas import IdentityRecord


def _from_account(user: dict[str, Any]) -> IdentityRecord:
    """认证账号 → 身份记录。能力位与作用域的判定与名单身份完全同构。"""
    last_seen = user["last_seen_at"]
    created_at = last_seen.isoformat() if last_seen is not None else ""
    return {
        "identity": str(user["username"]),
        "kind": IdentityKind.WEB,
        "capabilities": list(user["capabilities"]),
        "device_scope": list(user["device_scope"]),
        "label": "",
        "created_at": created_at,
    }


def optional_identity(
    request: Request,
    session: Session = Depends(get_db),
    account: dict[str, Any] | None = Depends(current_user),
) -> IdentityRecord | None:
    """会话 Cookie 优先，其次 Bearer 凭据，再次兼容期 X-Identity 头；都没有即匿名。

    Bearer 凭据只比对摘要，明文不落库、不进日志。各通道最终都产出同一个
    `IdentityRecord`，守卫逻辑不分叉。

    兼容期 X-Identity 分支为迁移过渡：旧调用方（网页端、外部脚本）仍发自定
    `X-Identity` 头，按头值查主体。该头明文可伪造，待网页端认证页与各调用方
    切换至 Bearer/会话后移除。
    """
    if account is not None:
        return _from_account(account)
    token = extract_bearer(request.headers.get("authorization"))
    if token is not None:
        name = principals_store.resolve_token(session, token)
        if name is None:
            raise ApiProblemError(UNKNOWN_IDENTITY)
        caller = service.resolve_identity(session, name)
        if caller is None:
            raise ApiProblemError(UNKNOWN_IDENTITY)
        return caller
    # 兼容期过渡（见 docstring）：头值即主体名，无效则退化为匿名，与历史行为一致。
    legacy = request.headers.get("x-identity")
    if legacy:
        caller = service.resolve_identity(session, legacy.strip())
        if caller is not None:
            return caller
    return None


def require_identity(caller: IdentityRecord | None = Depends(optional_identity)) -> IdentityRecord:
    """要求一个可解析的身份，不要求具体能力。操作端点与 require_admin 的基座。"""
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
