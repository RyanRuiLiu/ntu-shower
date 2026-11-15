"""认证授权域 API：会话建立、用户与密钥管理。

## 资源与形状

`/api/v1/auth/*` 是会话通道（登录、注销、当前账号）；`/api/v1/users` 是用户域：
名单与登记/编辑/撤销、密钥的签发/列表/吊销。`GET /users` 返回域读模型（权限字典
+ 名单），用户页一次装载。登记与签发密钥都返回明文凭据——只此一次，之后任何
响应不再携带。

## 守卫

整个用户域要求 `users:write`（标准 Bearer 密钥或会话，见 auth.dependencies）；
`/users/me` 是"我是谁"，任何已认证调用方可查——外部调用方用它验证自己的密钥
拿到的是哪个身份、哪些权限。

## 写入校验

scopes 必须在字典里；device_scope 里的设备必须真实存在——写错的授权永远执行
不了，挡在门口。不能删除自己的账号：管理台最后一名操作者删掉自己就把自己锁在
门外了。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Response, Security
from sqlalchemy.orm import Session

from app.contracts import Caller
from app.core.errors import problem_responses
from app.core.logging import log_event
from app.core.problems import (
    KEY_NOT_FOUND,
    UNAUTHENTICATED,
    UNKNOWN_SCOPE,
    USER_NOT_FOUND,
    VALIDATION_FAILED,
    ApiProblemError,
)
from app.db.deps import get_db
from app.modules.devices import crud as devices_store

from . import crud
from .dependencies import caller
from .permissions import ALL_SCOPES, SCOPE_LABELS
from .schemas import (
    KeyCreate,
    KeyIssued,
    KeyUpdate,
    KeyView,
    LoginRequest,
    MeView,
    ScopeOption,
    UserCatalog,
    UserCreate,
    UserKeys,
    UserUpdate,
    UserView,
)

#: 会话通道：登录、注销、当前账号。Cookie 的签名、下发与注销由 SessionMiddleware 承担。
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

#: 用户域里"我自己"那一面，要求已认证但不要求具体权限。要排在带 users:write 的
#: admin_router 之前注册（见 app/main.py），防 `/users/{username}` 抢走路由。
identity_router = APIRouter(prefix="/api/v1/users", tags=["users"])

admin_router = APIRouter(
    prefix="/api/v1/users",
    dependencies=[Security(caller, scopes=["users:write"])],
    tags=["users-admin"],
    responses=problem_responses(401, 403, 404, 422),
)


@router.post("/login", status_code=204, summary="口令登录", responses=problem_responses(401, 422))
def login(request: Request, body: LoginRequest, session: Session = Depends(get_db)) -> Response:
    """口令校验通过即签发会话（204 无体：账号事实由 GET /users/me 提供）。

    用户不存在与口令错误返回同一个问题类型，避免接口变成账号枚举器。会话字典
    记 `first_iat`（绝对超时的锚点）与 `iat`（滑动续期的锚点）。
    """
    user = crud.authenticate(session, body.username, body.password)
    if user is None:
        raise ApiProblemError(UNAUTHENTICATED, "用户名或口令不正确")
    now = time.time()
    request.session["u"] = str(user["username"])
    request.session["first_iat"] = now
    request.session["iat"] = now
    return Response(status_code=204)


@router.post("/logout", status_code=204, summary="退出登录")
def logout(request: Request) -> None:
    """注销即清空会话：SessionMiddleware 将置空后的会话下发为过期 Cookie。"""
    request.session.clear()


@identity_router.get("/me", response_model=MeView, summary="当前调用方身份", responses=problem_responses(401))
def who(caller: Caller = Depends(caller)) -> MeView:
    """调用方自身视图：账号身份与权限集合。外部调用方据此验证自己的密钥。"""
    return MeView(
        username=caller["username"],
        name=caller["name"],
        scopes=caller["scopes"],
        device_scope=caller["device_scope"],
        key_name=caller["key_name"],
    )


@admin_router.get("", response_model=UserCatalog, summary="用户与权限字典")
def list_users(session: Session = Depends(get_db)) -> UserCatalog:
    """用户域读模型：权限字典 + 用户名单，用户页一次装载。"""
    return UserCatalog(
        scopes=[ScopeOption(scope=scope, label=SCOPE_LABELS[scope]) for scope in sorted(ALL_SCOPES)],
        users=[UserView(**user) for user in crud.list_users(session)],
    )


@admin_router.post("", response_model=UserView, summary="登记用户")
def create_user(req: UserCreate, session: Session = Depends(get_db)) -> UserView:
    """登记一个用户。username 与既有用户重名时整体拒绝（编辑请用 PUT）。"""
    if crud.find_row(session, req.username) is not None:
        raise ApiProblemError(VALIDATION_FAILED, "该用户名已存在，编辑请用 PUT")
    user = crud.create_user(session, req.username, req.name, req.password)
    log_event("INFO", "auth.user-created", "用户已登记", username=req.username)
    return UserView(**user)


@admin_router.put("/{username}", response_model=UserView, summary="编辑用户")
def update_user(username: str, req: UserUpdate, session: Session = Depends(get_db)) -> UserView:
    """编辑用户：显示名、禁用位与口令（设置/重置）。禁用即时生效。"""
    user = crud.update_user(session, username, req.name, req.disabled, req.password)
    if user is None:
        raise ApiProblemError(USER_NOT_FOUND, username)
    log_event("INFO", "auth.user-updated", "用户已更新", username=username)
    return UserView(**user)


@admin_router.delete("/{username}", status_code=204, summary="删除用户")
def delete_user(username: str, caller: Caller = Depends(caller), session: Session = Depends(get_db)) -> Response:
    """删除用户（名下密钥一并删除）。不能删除自己的账号。"""
    if caller["username"] == username:
        raise ApiProblemError(VALIDATION_FAILED, "不能删除自己的账号")
    removed = crud.delete_user(session, username)
    if removed:
        log_event("INFO", "auth.user-deleted", "用户已删除", username=username)
    return Response(status_code=204)


@admin_router.post("/{username}/keys", response_model=KeyIssued, summary="签发密钥")
def issue_key(username: str, req: KeyCreate, session: Session = Depends(get_db)) -> KeyIssued:
    """为用户签发一枚密钥：权限集与设备作用域均附于密钥，明文仅出现一次。"""
    _validated_scopes(req.scopes)
    _validated_device_scope(session, req.device_scope)
    issued = crud.issue_key(session, username, req.name, req.scopes, req.device_scope)
    if issued is None:
        raise ApiProblemError(USER_NOT_FOUND, username)
    view, token = issued
    log_event("INFO", "auth.key-issued", "密钥已签发", username=username, scopes=",".join(req.scopes))
    return KeyIssued(**view, token=token)


@admin_router.get("/{username}/keys", response_model=UserKeys, summary="用户的密钥清单")
def list_keys(username: str, session: Session = Depends(get_db)) -> UserKeys:
    """该用户的全部密钥（含已吊销：吊销历史本身即为需要保留的事实）。"""
    if crud.find_row(session, username) is None:
        raise ApiProblemError(USER_NOT_FOUND, username)
    return UserKeys(keys=[KeyView(**key) for key in crud.list_keys(session, username)])


@admin_router.put("/{username}/keys/{key_id}", response_model=KeyView, summary="编辑密钥")
def update_key(username: str, key_id: int, req: KeyUpdate, session: Session = Depends(get_db)) -> KeyView:
    """编辑密钥：名称、权限集与设备作用域，令牌不变，下一次请求即生效（Stripe RAK 形态）。"""
    if req.scopes is not None:
        _validated_scopes(req.scopes)
    if req.device_scope is not None:
        _validated_device_scope(session, req.device_scope)
    key = crud.find_key(session, username, key_id)
    if key is None:
        raise ApiProblemError(KEY_NOT_FOUND, f"{username}/{key_id}")
    if key["revoked_at"]:
        raise ApiProblemError(VALIDATION_FAILED, "已吊销的密钥不能编辑")
    updated = crud.update_key(session, username, key_id, req.name, req.scopes, req.device_scope)
    if updated is None:  # 预检后被并发吊销：同一条"不能编辑"的语义
        raise ApiProblemError(VALIDATION_FAILED, "已吊销的密钥不能编辑")
    log_event("INFO", "auth.key-updated", "密钥已更新", username=username, key_id=key_id)
    return KeyView(**updated)


@admin_router.delete("/{username}/keys/{key_id}", status_code=204, summary="吊销密钥")
def revoke_key(username: str, key_id: int, session: Session = Depends(get_db)) -> Response:
    """吊销单枚密钥：该密钥自下一次请求起返回 401，同一用户的其他密钥不受影响。幂等。"""
    crud.revoke_key(session, username, key_id)
    return Response(status_code=204)


def _validated_scopes(scopes: list[str]) -> None:
    """权限集必须在字典里，且不能为空（空集与未授权没有区别）。"""
    if not scopes:
        raise ApiProblemError(VALIDATION_FAILED, "至少选择一个权限")
    unknown = sorted(set(scopes) - ALL_SCOPES)
    if unknown:
        raise ApiProblemError(UNKNOWN_SCOPE, f"未知权限：{unknown}")


def _validated_device_scope(session: Session, device_scope: list[str]) -> None:
    """作用域里的设备必须真实存在：写错的授权永远执行不了。"""
    known = {record["device_id"] for record in devices_store.list_all(session)}
    unknown = sorted(set(device_scope) - known - {"*"})
    if unknown:
        raise ApiProblemError(VALIDATION_FAILED, f"作用域含未知设备：{unknown}")


__all__: list[str] = ["admin_router", "identity_router", "router"]
