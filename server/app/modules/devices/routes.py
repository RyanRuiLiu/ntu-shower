"""设备域 API：精简清单（公开）、运维档案与远程操作（admin）。

## 路径规则

资源用复数名词；对单个资源施加动作用 `<资源>/{id}/<动词>`；改档案用 PATCH
带字段。目标设备一律在路径里，请求体只放动作参数。

## 两种表示，各自一个端点

- `GET /devices`：精简视图（id、名称、在线、actions）——查看语义，公开；
  `actions` 由 auth.allowed_actions 按调用方权限与作用域算出，客户端不复制
  判定规则。
- `GET /devices/{device_id}`：单台运维档案（版本、信任、IP、时钟偏差等诊断
  字段），devices:read 专属，管理台设备表专用。

## 守卫

管理面要求 `devices:write`；对单台设备的端点额外做对象级 device_scope 检查，
下发命令的端点还要求设备已信任且在线（require_trusted_online_device）——把命令
发给离线设备只会白等超时。
"""

import hmac

from fastapi import APIRouter, Depends, Query, Request, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard, optional_caller
from app.auth.permissions import allowed_actions
from app.contracts import Caller, CommandOutcome, DeviceLogKind
from app.core import actions
from app.core.clock import now_rfc3339
from app.core.config import settings
from app.core.connections import manager
from app.core.errors import problem_responses
from app.core.outcomes import OK as OUTCOME_OK
from app.core.problems import (
    DEVICE_LOG_READ_FAILED,
    DEVICE_NOT_FOUND,
    INVALID_DEVICE_CLAIM,
    VALIDATION_FAILED,
    ApiProblemError,
)
from app.core.protocol import as_int
from app.core.tokens import extract_bearer, hash_token
from app.db.base import utcnow
from app.db.deps import get_db

from . import crud, schemas, service
from .actions import reject_overloaded
from .dependencies import DEVICE_ACTION_TIMEOUT_S, device_action
from .schemas import (
    DeviceClockSetRequest,
    DeviceExecRequest,
    DeviceListItem,
    DeviceOrderRequest,
    DevicePatchRequest,
    DeviceRecord,
    DeviceUiKeyRequest,
    DeviceUiTextRequest,
    DeviceUiTouchRequest,
)

router = APIRouter(prefix="/api/v1", tags=["devices"], responses=problem_responses(422))


@router.post(
    "/devices/enroll",
    response_model=schemas.DeviceEnrollResult,
    summary="设备自注册（claim 换凭据）",
    responses=problem_responses(401),
)
def enroll_device(
    req: schemas.DeviceEnrollRequest, request: Request, session: Session = Depends(get_db)
) -> schemas.DeviceEnrollResult:
    """设备自注册：APK 构建期内置一枚全队共享的 claim 令牌，设备首次连接以此
    换取自身专属的接入凭据（AWS IoT fleet provisioning by claim 同型）。

    - 通道开关在服务端配置（`DEVICE_ENROLL_TOKEN`）：留空即 fail closed，一律 401；
    - 注册仅证明「来自本系统的客户端」，**不等于信任**——信任位仍由管理员在
      设备档案上设置（ADR-0007 模型不变），未信任设备的注册响应仍为
      `trusted=false`，业务帧仍被网关拒绝；
    - 每次注册先吊销该设备旧凭据再签发新凭据：重装 APK、凭据丢失均可自助恢复，
      轮换语义与 `--token-output` 签发的一致；
    - 已在册设备的档案字段照常刷新，trusted 位保持不变。

    凭据明文只出现在本次响应；服务端以哈希后常量时间比较，不暴露时序。
    """
    claim = extract_bearer(request.headers.get("authorization"))
    configured = settings.device_enroll_token
    if not configured or claim is None or not hmac.compare_digest(hash_token(claim), hash_token(configured)):
        raise ApiProblemError(INVALID_DEVICE_CLAIM, "注册令牌缺失或无效")

    existing = crud.get(session, req.device_id)
    if existing is not None and existing["retired"]:
        raise ApiProblemError(VALIDATION_FAILED, "设备已停用，不能重新注册")

    crud.register(
        session,
        device_id=req.device_id,
        app_version=req.app_version,
        version_code=req.version_code,
        os_version=req.os_version,
        capabilities=req.capabilities,
        ip=request.client.host if request.client else "",
        booted_at=utcnow(),
        seen_at=utcnow(),
    )
    crud.revoke_all(session, req.device_id)
    token = crud.issue(session, req.device_id, label="自注册")
    if token is None:  # register 刚确保了行存在，到这里为 None 只可能是并发除档
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {req.device_id} 刚被除档")
    return schemas.DeviceEnrollResult(device_id=req.device_id, token=token)


#: 设备管理面分两个 router：对设备集合的（runtime、order）只要求权限；对单台设备的
#: 全部端点额外做对象级 device_scope 检查（OWASP IDOR）——路由级依赖取路径参数，
#: 所以按"路径里有没有 {device_id}"划分。
admin_router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Security(caller, scopes=["devices:write"])],
    tags=["devices-admin"],
    responses=problem_responses(401, 403, 422),
)
device_router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Security(caller, scopes=["devices:write"]), Depends(device_scope_guard)],
    tags=["devices-admin"],
    responses=problem_responses(401, 403, 404, 409, 422),
)
device_read_router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Security(caller, scopes=["devices:read"]), Depends(device_scope_guard)],
    tags=["devices-admin"],
    responses=problem_responses(401, 403, 404, 422),
)


def _ui_timeout_s() -> float:
    """界面操作的等待上限。

    每个 `ui.*` 命令都要等设备完成一次节点遍历或一次手势注入，耗时随界面复杂度
    变化。曾是配置键（运维从未改过，2026-11-17 精简收编为常量）。
    """
    return float(DEVICE_ACTION_TIMEOUT_S)


# ── 查询 ──


@router.get("/devices", summary="设备清单（含该调用方可执行的动作）")
def list_devices(
    identity: Caller | None = Depends(optional_caller), session: Session = Depends(get_db)
) -> list[DeviceListItem]:
    """精简设备清单：id、名称、在线，以及该调用方对每台可执行的用户动作。

    匿名调用 actions 恒为空；判定经 auth 的唯一实现，客户端不复制作用域规则。
    """
    items: list[DeviceListItem] = []
    # 权限在循环外取一次：匿名调用没有身份，取不到就是空表，actions 随后由作用域否决
    scopes = identity["scopes"] if identity is not None else []
    for record in service.list_devices(session):
        in_scope = identity is not None and (
            "*" in identity["device_scope"] or record["device_id"] in identity["device_scope"]
        )
        items.append(
            {
                **service.to_summary(record),
                "actions": allowed_actions(scopes) if in_scope else [],
            }
        )
    return items


@device_read_router.get("/{device_id}/logs", summary="设备最近一次上送的日志")
def get_device_logs(
    device_id: str, kind: DeviceLogKind = DeviceLogKind.DEVICE, session: Session = Depends(get_db)
) -> schemas.DeviceLogView:
    """该设备最近一次上送的日志（kind=device / crash / ota）。

    返回的是设备离线期间留存、重连时上送的副本：设备在每次握手、变化后的周期
    与关键错误后都会更新；读取设备当前的日志原文使用 `/logs/live`。
    设备从未上送过该类日志时 404。
    """
    _require_device(session, device_id)
    latest = service.latest_device_log(session, device_id, kind)
    if latest is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 尚无 {kind} 日志上送")
    return schemas.DeviceLogView.model_validate(latest)


@device_read_router.get("/{device_id}/logs/live", summary="设备日志尾部现读", responses=problem_responses(409, 502))
async def get_live_device_log(
    device_id: str,
    kind: DeviceLogKind = DeviceLogKind.DEVICE,
    lines: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
) -> schemas.DeviceLogLiveView:
    """从设备现读日志尾部；设备离线时返回 409，调用方转而展示最近一次上送内容。

    `present=false` 不是错误：crash.log 只在真正崩溃后才会出现，ota-install.log 只在
    发起过安装后才会出现；调用方据此提示「设备上暂无该日志」。
    """
    outcome = await device_action(
        session,
        device_id,
        actions.DEVICE_LOG_TAIL,
        {"kind": kind, "lines": lines},
        DEVICE_ACTION_TIMEOUT_S,
    )
    if outcome["status"] != OUTCOME_OK.value:
        raise ApiProblemError(
            DEVICE_LOG_READ_FAILED,
            outcome["terminal_message"] or "设备未返回日志内容",
        )
    data = outcome["data"]
    if not isinstance(data, dict):
        raise ApiProblemError(DEVICE_LOG_READ_FAILED, "设备返回的日志结果为空")
    return schemas.DeviceLogLiveView(
        content=str(data.get("content", "")),
        kind=str(data.get("kind", kind)),
        lines=as_int(data.get("lines"), 0),
        truncated=bool(data.get("truncated", False)),
        present=bool(data.get("present", False)),
        size_bytes=as_int(data.get("size_bytes"), 0),
        read_at=now_rfc3339(),
    )


@device_read_router.get("/{device_id}", summary="单台设备运维档案")
def get_device(device_id: str, session: Session = Depends(get_db)) -> DeviceRecord:
    """单台设备的运维档案（版本、信任、来源 IP、时钟偏差等诊断字段）。

    列表（GET /devices）是公开摘要；单项完整档案是管理面（devices:read）。
    """
    return _require_device(session, device_id)


@admin_router.patch("", response_model=schemas.DeviceOrderResponse, summary="设备展示顺序")
def patch_device_order(req: DeviceOrderRequest, session: Session = Depends(get_db)) -> schemas.DeviceOrderResponse:
    """按给定顺序重排设备：设备下拉、管理台设备表、统计接口全部照此排列。

    清单必须完整（未列出的设备保持原位），出现未知设备整体拒绝。
    """
    service.set_order(session, req.order)
    return schemas.DeviceOrderResponse(order=[str(record["device_id"]) for record in service.list_devices(session)])


@device_router.patch("/{device_id}", summary="改设备档案（改名/信任/停用）")
async def patch_device(device_id: str, req: DevicePatchRequest, session: Session = Depends(get_db)) -> DeviceRecord:
    """修改设备档案：改名、改信任位，仅修改请求中出现的字段。

    修改完成后发布一条 `device.update`（精简载荷）：其它已打开的页面依赖它更新
    设备名与在线标记，否则需等待轮询。

    **修改信任位必须断开现有连接**：信任只在 hello 握手时判定一次，在线设备的
    旧连接不会自动获得新信任——其业务帧与命令结果会被静默丢弃，表现为「设备
    在线、功能全部失效」。断开后设备按重连退避在数秒内重新接入，以新信任重新
    握手，startup 全貌帧随之补上。
    """
    if req.name is not None:
        service.rename_device(session, device_id, req.name)
    if req.trusted is not None:
        service.set_trusted(session, device_id, req.trusted)
        await manager.disconnect(device_id)
    if req.retired is not None:
        service.set_retired(session, device_id, req.retired)
        # 在线设备的业务帧不会因这个位自动停下，掐断让它按重连退避回来——
        # 那时握手即被拒（已停用），数据流就此停止。
        await manager.disconnect(device_id)
    record = _require_device(session, device_id)
    service.publish_device_update(session, device_id)
    return record


# ── 远程动作 ──


@device_router.post("/{device_id}/exec", summary="远程执行 shell 命令")
async def exec_command(device_id: str, req: DeviceExecRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """远程调试：在设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    return await device_action(
        session, device_id, actions.DEVICE_EXEC, {"command": req.command}, DEVICE_ACTION_TIMEOUT_S
    )


@device_router.post("/{device_id}/capture", summary="抓取设备屏幕")
async def capture_screen(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """抓取设备全屏画面，结果在 data.image_base64（JPEG，NO_WRAP）。

    设备端采用 screenrecord 加 MediaCodec 解码首帧：本机 framebuffer 与
    screencap 均取不到画面（硬件合成器直出）。
    """
    return await device_action(session, device_id, actions.SCREEN_CAPTURE, {}, DEVICE_ACTION_TIMEOUT_S)


@router.post(
    "/devices/{device_id}/photo",
    dependencies=[Security(caller, scopes=["devices:photo"]), Depends(device_scope_guard)],
    summary="摄像头拍照",
    responses=problem_responses(401, 403, 404, 409),
)
async def photo(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """摄像头拍照。要求 devices:photo 且设备在密钥作用域内。

    挂在设备域：拍照是设备能力，与它操作的是哪场会话无关；屏幕抓取（/capture）
    与摄像头拍照（/photo）在这里并列。
    """
    outcome = await device_action(session, device_id, actions.CAMERA_CAPTURE, {}, settings.photo_timeout_s)
    return reject_overloaded(outcome)


@device_router.post("/{device_id}/restart-app", summary="重启应用进程")
async def restart_app(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启 NTU Shower 进程（不重启整机，亦不重启浴室 App）。"""
    return await device_action(session, device_id, actions.APP_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


@device_router.post("/{device_id}/clock-set", summary="设置设备时钟")
async def set_clock(device_id: str, req: DeviceClockSetRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """设置设备系统时钟（datetime 为设备本地墙钟时间）。浴室 App 的每日开关机计划依赖它。"""
    return await device_action(
        session, device_id, actions.CLOCK_SET, {"datetime": req.datetime}, DEVICE_ACTION_TIMEOUT_S
    )


@device_router.post("/{device_id}/restart-bath", summary="重启浴室 App")
async def restart_bath_app(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启浴室 App：先强制停止再冷启动。仅用于排障；延长服务流程已改用 power.schedule。"""
    return await device_action(session, device_id, actions.BATH_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


@device_router.post("/{device_id}/reboot", summary="重启整机")
async def reboot_device(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启整机。设备重启后 agent 重新接入，会话与喷头现态由重连后的全貌帧重建。"""
    return await device_action(session, device_id, actions.DEVICE_REBOOT, {}, DEVICE_ACTION_TIMEOUT_S)


# ── 设备界面 ──
# 这一组是"看到并操作设备界面"：dump 给结构（节点树），touch/key/input
# 给动作，与 capture（一张图）互补。排查"设备卡在某个弹窗"时节点树比截图有用。


@device_router.post("/{device_id}/ui-dump", summary="导出界面节点树")
async def dump_ui_tree(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """导出设备当前界面的节点树，结果在 data.xml。

    格式与 uiautomator dump 一致；节点上限取配置项 ui_dump_max_nodes。
    """
    return await device_action(session, device_id, actions.UI_DUMP, {}, _ui_timeout_s())


@device_router.post("/{device_id}/ui-touch", summary="按坐标点击或滑动")
async def touch(device_id: str, req: DeviceUiTouchRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """按屏幕坐标点击或滑动：未提供 to_x/to_y 时为点击，提供时为滑动。

    定位先经 dump 读取节点树、由 bounds 求中心点，不使用遍历序号。
    """
    params: dict[str, int] = {"x": req.x, "y": req.y}
    if req.to_x is not None and req.to_y is not None:
        params["to_x"] = req.to_x
        params["to_y"] = req.to_y
    if req.duration_ms is not None:
        params["duration_ms"] = req.duration_ms
    return await device_action(session, device_id, actions.UI_TOUCH, params, _ui_timeout_s())


@device_router.post("/{device_id}/ui-key", summary="发送系统按键")
async def send_key(device_id: str, req: DeviceUiKeyRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """发送系统按键（闭集，路由层以 422 拒绝非法值）。"""
    return await device_action(session, device_id, actions.UI_KEY, {"key": req.key}, _ui_timeout_s())


@device_router.post("/{device_id}/ui-input", summary="注入文本")
async def input_text(device_id: str, req: DeviceUiTextRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """向当前焦点控件输入文本；channel 在两条并列通路中显式选择。"""
    return await device_action(
        session,
        device_id,
        actions.UI_INPUT,
        {"text": req.text, "channel": req.channel},
        _ui_timeout_s(),
    )


def _require_device(session: Session, device_id: str) -> DeviceRecord:
    """取设备档案；不存在时 404，不用成功响应体里的 found: false 掩盖错误。"""
    device = service.get_device(session, device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return device
