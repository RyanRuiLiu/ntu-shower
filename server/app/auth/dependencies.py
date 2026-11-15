"""守卫：凭据解析与 scope 判定（全库唯一的实现）。

调用方通过标准 `Authorization: Bearer` 凭据或签名会话 Cookie 表明身份，服务端
查 users / api_keys 得权限集与设备作用域。两条通道产出同一个 `Caller`，守卫
逻辑不分叉。

## 守卫形态

- `caller`：要求已认证，并按依赖树聚合出的所需 scopes 判定（FastAPI 官方
  `SecurityScopes` 机制；scope 是 RFC 6749 官方术语，密钥管理页与 OpenAPI
  渲染同一套词）。失败码对齐 RFC 6750 §3.1：凭据无效 invalid-token（401）、
  权限不足 insufficient-scope（403）。
- `optional_caller`：带凭据就解析（无效 401），不带即匿名。公开端点据此裁剪
  视图（如 /devices 的 actions）。
- `device_scope_guard`：对象级检查——路径里的设备必须在该调用方的 device_scope
  内（OWASP IDOR 条目）。挂在路径含 `{device_id}` 的 router 上。
- `require_trusted_online_device`：设备面的闸（存在 + 信任 + 在线），config、
  bridge、devices 的写路径共用。

## 两个通道，一个形状

- 会话通道（面板操作者）：有口令的用户即操作者，scopes 恒 `["*"]`——权限只存
  密钥一处，面板不产生第二份权限存储；
- 密钥通道：scopes 与 device_scope 都取该枚密钥（GitHub fine-grained PAT 同型），
  权限附在密钥上。

## 会话生命周期（OWASP Session CS 的三项机制，全部服务端强制）

- 空闲超时：Cookie `max_age = session_idle_seconds`，浏览器弃 + 签名端拒；
- renewal：使用中每过 RENEW_AFTER_S 重签一次 Cookie，活跃用户不因空闲超时掉线；
- 绝对超时：距首次登录超过 `session_absolute_seconds` 即失效，服务端以
  `first_iat` 强制。
"""

import time

from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from sqlalchemy.orm import Session

from app.contracts import Caller, DeviceRecord, KeyRecord, UserRecord
from app.core.config import settings
from app.core.connections import manager
from app.core.problems import (
    DEVICE_NOT_FOUND,
    DEVICE_OFFLINE,
    INSUFFICIENT_SCOPE,
    INVALID_TOKEN,
    UNAUTHENTICATED,
    UNTRUSTED_DEVICE,
    ApiProblemError,
)
from app.core.tokens import extract_bearer
from app.db.deps import get_db
from app.modules.devices import crud as devices_store

from . import crud
from .permissions import SCOPE_LABELS, Scope, scopes_satisfied

#: 使用中的会话每过这么久重签一次 Cookie（滑动续期）。1 天：活跃用户无感续期，
#: 空闲超过 idle 的会话由 Cookie 寿命兜底。
RENEW_AFTER_S = 24 * 60 * 60

#: OpenAPI 的安全方案声明。OAuth2 Password flow 是 RFC 6749 定义、OpenAPI 原生
#: 支持的两种凭据通道之一（另一种是 http bearer）：
#: - API 调用方持 `ntushower_` 密钥，`Authorization: Bearer <密钥>` 调用——密钥由
#:   管理台在用户与密钥页签发（users:write），scopes 即权限字典，与 HTTP 头里
#:   实际传的凭据一一对应；
#: - 管理台浏览器走同一 flow 的 password grant（tokenUrl 指向登录端点）换会话
#:   Cookie，对 OpenAPI 而言两种通道汇合为同一个 securityScheme。
#: 实现层不消费 oauth2_scheme 的返回值——提取走 extract_bearer，Cookie 通道经
#: current_user，auto_error=False 让两条通道在同一依赖里汇合。
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    scheme_name="ntu_shower_auth",
    scopes={scope.value: SCOPE_LABELS[scope.value] for scope in Scope},
    auto_error=False,
)


def _session_renew(request: Request) -> bool:
    """滑动续期与绝对超时（服务端强制）。到期即清空会话并返回 False。"""
    now = time.time()
    first = request.session.get("first_iat")
    if first is None:
        request.session["first_iat"] = now
        request.session["iat"] = now
        return True
    if now - first > settings.session_absolute_seconds:
        request.session.clear()
        return False
    if now - request.session.get("iat", first) > RENEW_AFTER_S:
        request.session["iat"] = now
    return True


def current_user(request: Request, session: Session = Depends(get_db)) -> UserRecord | None:
    """当前已认证账号（会话通道）；无有效会话、签名失效、账号被禁用都返回 None。

    禁用位每请求查库：禁用账号立即失效，不等会话过期。
    """
    username = request.session.get("u")
    if not isinstance(username, str) or not username:
        return None
    if not _session_renew(request):
        return None
    user = crud.find_user(session, username)
    if user is None or user["disabled"]:
        return None
    return user


def _from_session(user: UserRecord) -> Caller:
    return {
        "username": user["username"],
        "name": user["name"],
        "scopes": [Scope.WILDCARD],
        "device_scope": [Scope.WILDCARD],
        "key_name": None,
    }


def _from_key(user: UserRecord, key: KeyRecord) -> Caller:
    return {
        "username": user["username"],
        "name": user["name"],
        "scopes": key["scopes"],
        "device_scope": key["device_scope"],
        "key_name": key["name"],
    }


def _resolve(
    request: Request,
    session: Session,
    account: UserRecord | None,
    token: str | None = None,
) -> Caller | None:
    """会话 Cookie 优先，其次 Bearer 凭据；都没有即匿名。

    Bearer 凭据只比对摘要，明文不落库、不进日志（审计只记摘要前 12 位）。
    """
    if account is not None:
        return _from_session(account)
    token = token or extract_bearer(request.headers.get("authorization"))
    if token is not None:
        resolved = crud.resolve_key(session, token)
        if resolved is None:
            raise ApiProblemError(INVALID_TOKEN)
        return _from_key(resolved[0], resolved[1])
    return None


def optional_caller(
    request: Request,
    session: Session = Depends(get_db),
    account: UserRecord | None = Depends(current_user),
) -> Caller | None:
    """带凭据就解析（无效 401），不带即匿名。公开端点据此裁剪视图。

    它不挂 oauth2_scheme：公开端点在 OpenAPI 里不得出现 security 要求。
    """
    return _resolve(request, session, account)


def caller(
    security_scopes: SecurityScopes,
    request: Request,
    session: Session = Depends(get_db),
    account: UserRecord | None = Depends(current_user),
    token: str | None = Depends(oauth2_scheme),
) -> Caller:
    """要求已认证，并判定依赖树聚合出的所需 scopes（FastAPI 官方聚合机制）。

    所需 scopes 由端点与 router 上的 `Security(caller, scopes=[...])` 声明，这里
    只做聚合后的判定。oauth2_scheme 是本依赖的子依赖（SecurityBase）：OpenAPI
    据它在每个操作上渲染 security 要求与聚合的 scopes，外部调用方拿到的契约
    才有权限语义。
    """
    identity = _resolve(request, session, account, token)
    if identity is None:
        raise ApiProblemError(UNAUTHENTICATED)
    if not scopes_satisfied(identity["scopes"], security_scopes.scopes):
        raise ApiProblemError(INSUFFICIENT_SCOPE, f"需要权限：{security_scopes.scopes}")
    return identity


def device_scope_guard(device_id: str, caller: Caller = Depends(caller)) -> Caller:
    """对象级检查：目标设备必须在该调用方的 device_scope 内。

    403 不是 404：设备存在但不在作用域，说"没这设备"会诱导调用方换 id 撞库。
    """
    scope = caller["device_scope"]
    if Scope.WILDCARD not in scope and device_id not in scope:
        raise ApiProblemError(INSUFFICIENT_SCOPE, f"作用域不含设备：{device_id}")
    return caller


def require_trusted_online_device(device_id: str, session: Session) -> DeviceRecord:
    """设备面守卫：设备必须存在且已信任、当前在线；否则拒绝进入业务。

    住在守卫层而不是 devices.dependencies：config、bridge、sessions 的写路径都要
    先过这道闸，而 devices 域自身要读 config.store——守卫若放设备域，config →
    devices → config 就是包级循环。设备存在性与信任位经持久化层读取，在线与否看
    连接表，全程不 import devices 包。
    """
    record = devices_store.get(session, device_id)
    if record is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    if not record["trusted"]:
        # 设备存在但未信任是 403，不是 404：混淆两者会让客户端以为设备不存在
        raise ApiProblemError(UNTRUSTED_DEVICE, f"设备 {device_id} 尚未被信任")
    if not manager.is_online(device_id):
        raise ApiProblemError(DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")
    return record


__all__: list[str] = [
    "RENEW_AFTER_S",
    "caller",
    "current_user",
    "device_scope_guard",
    "oauth2_scheme",
    "optional_caller",
    "require_trusted_online_device",
]
