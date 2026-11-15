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
from app.contracts import Caller, CommandOutcome
from app.core import actions
from app.core.clock import now_rfc3339
from app.core.config import settings
from app.core.connections import manager
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
from app.modules.config import store as config_store
from app.modules.devices import crud as devices_store

from . import schemas, service
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

router = APIRouter(prefix="/api/v1", tags=["devices"])


@router.post("/devices/enroll", response_model=schemas.DeviceEnrollResult)
def enroll_device(
    req: schemas.DeviceEnrollRequest, request: Request, session: Session = Depends(get_db)
) -> schemas.DeviceEnrollResult:
    """设备自注册：APK 构建期内置一枚全队共享的 claim 令牌，设备首次连接凭它换
    每台自己的接入凭据（AWS IoT fleet provisioning by claim 同型）。

    - 通道开关在服务端配置（`DEVICE_ENROLL_TOKEN`）：留空即 fail closed，一律 401；
    - 注册只回答「这是我们的程序」，**不等于信任**——信任位照旧由管理员在档案上
      点（ADR-0007 模型不动），未信任设备照旧收 `trusted=false`、业务帧被墙；
    - 每次注册先吊销该设备旧凭据再签发新的：重装 APK、凭据丢失都能自助恢复，
      轮换语义与 `--token-output` 签发的一致；
    - 已在册设备（含转换搬来的 6 台）档案字段照常刷新、trusted 位不动。

    凭据明文只出现在本次响应；比对用哈希后常量时间比较，不暴露时序。
    """
    claim = extract_bearer(request.headers.get("authorization"))
    configured = settings.device_enroll_token
    if not configured or claim is None or not hmac.compare_digest(hash_token(claim), hash_token(configured)):
        raise ApiProblemError(INVALID_DEVICE_CLAIM, "注册令牌缺失或无效")

    devices_store.register(
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
    devices_store.revoke_all(session, req.device_id)
    token = devices_store.issue(session, req.device_id, label="自注册")
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
)
device_router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Security(caller, scopes=["devices:write"]), Depends(device_scope_guard)],
    tags=["devices-admin"],
)
device_read_router = APIRouter(
    prefix="/api/v1/devices",
    dependencies=[Security(caller, scopes=["devices:read"]), Depends(device_scope_guard)],
    tags=["devices-admin"],
)

#: 可查看/上送的三份设备日志。
LOG_KINDS = ("device", "crash", "ota")


def _ui_timeout_s(session: Session, device_id: str) -> float:
    """界面操作的等待上限，取该设备的配置项 `ui_operation_timeout_s`。

    每个 `ui.*` 命令都要等设备完成一次节点遍历或一次手势注入，耗时随界面复杂度
    变化；做成配置项后现场改一次即对全部设备生效。
    """
    timeout_s = as_int(
        config_store.effective(session, device_id)["ui_operation_timeout_s"], int(DEVICE_ACTION_TIMEOUT_S)
    )
    return float(timeout_s)


# ── 查询 ──


@router.get("/devices")
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


@device_read_router.get("/{device_id}/logs")
def get_device_logs(device_id: str, kind: str = "device", session: Session = Depends(get_db)) -> schemas.DeviceLogView:
    """该设备最近一次上送的日志（kind=device / crash / ota）。

    上送的是离线时留下的副本：设备每次握手、变化后的周期与关键错误后都会更新；
    要看此刻设备上的原文走 `/logs/live`。设备从未上送过该类日志时 404。
    """
    if kind not in LOG_KINDS:
        raise ApiProblemError(VALIDATION_FAILED, f"日志种类 {kind}（应为 {' / '.join(LOG_KINDS)}）")
    _require_device(session, device_id)
    latest = service.latest_device_log(session, device_id, kind)
    if latest is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 尚无 {kind} 日志上送")
    return schemas.DeviceLogView.model_validate(latest)


@device_read_router.get("/{device_id}/logs/live")
async def get_live_device_log(
    device_id: str,
    kind: str = "device",
    lines: int = Query(default=200, ge=1, le=500),
    session: Session = Depends(get_db),
) -> schemas.DeviceLogLiveView:
    """从设备现读日志尾部；设备离线时返回 409，页面再显示上次上送内容。

    `present=false` 不是错误：crash.log 只在真正崩溃后才会出现，ota-install.log 只在
    发起过安装后才会出现；页面据此显示"设备上暂无该日志"。
    """
    if kind not in LOG_KINDS:
        raise ApiProblemError(VALIDATION_FAILED, f"日志种类 {kind}（应为 {' / '.join(LOG_KINDS)}）")
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


@device_read_router.get("/{device_id}")
def get_device(device_id: str, session: Session = Depends(get_db)) -> DeviceRecord:
    """单台设备的运维档案（版本、信任、来源 IP、时钟偏差等诊断字段）。

    列表（GET /devices）是公开摘要；单项完整档案是管理面（devices:read）。
    """
    return _require_device(session, device_id)


@admin_router.patch("", response_model=schemas.DeviceOrderResponse)
def patch_device_order(req: DeviceOrderRequest, session: Session = Depends(get_db)) -> schemas.DeviceOrderResponse:
    """按给定顺序重排设备：设备下拉、管理台设备表、统计接口全部照此排列。

    清单必须完整（漏掉的设备保持原位），出现未知设备整体拒绝。
    """
    service.set_order(session, req.order)
    return schemas.DeviceOrderResponse(order=[str(record["device_id"]) for record in service.list_devices(session)])


@device_router.patch("/{device_id}")
async def patch_device(device_id: str, req: DevicePatchRequest, session: Session = Depends(get_db)) -> DeviceRecord:
    """改设备档案：改名、改信任位，只改传了的字段。

    改完推一条 `device.update`（精简载荷）：其它打开的页面要靠它更新设备名与
    在线标记，不推就要等轮询。

    **改信任位必须掐断现有连接**：信任只在 hello 握手时判定一次，在线设备的旧连接
    不会自动带上新信任——它的业务帧与命令结果会被静默丢弃，表现是「设备在线、功能
    全坏」。掐掉之后设备按重连退避几秒内回来，带着新信任重新握手，startup 全貌帧
    随之补上。
    """
    if req.name is not None:
        service.rename_device(session, device_id, req.name)
    if req.trusted is not None:
        service.set_trusted(session, device_id, req.trusted)
        await manager.disconnect(device_id)
    record = _require_device(session, device_id)
    service.publish_device_update(session, device_id)
    return record


# ── 远程动作 ──


@device_router.post("/{device_id}/exec")
async def exec_command(device_id: str, req: DeviceExecRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """远程调试：在设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    return await device_action(
        session, device_id, actions.DEVICE_EXEC, {"command": req.command}, DEVICE_ACTION_TIMEOUT_S
    )


@device_router.post("/{device_id}/capture")
async def capture_screen(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """抓取设备全屏画面，结果在 data.image_base64（JPEG，NO_WRAP）。

    设备端走 screenrecord + MediaCodec 解码首帧：本机 framebuffer 与 screencap
    取不到画面（硬件合成器直出）。
    """
    return await device_action(session, device_id, actions.SCREEN_CAPTURE, {}, DEVICE_ACTION_TIMEOUT_S)


@device_router.post("/{device_id}/restart-app")
async def restart_app(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启 NTU Shower 进程（不重启整机，也不动浴室 APP）。"""
    return await device_action(session, device_id, actions.APP_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


@device_router.post("/{device_id}/clock-set")
async def set_clock(device_id: str, req: DeviceClockSetRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """设置设备系统时钟（datetime 为设备本地墙钟时间）。浴室 App 的每日开关机计划依赖它。"""
    return await device_action(
        session, device_id, actions.CLOCK_SET, {"datetime": req.datetime}, DEVICE_ACTION_TIMEOUT_S
    )


@device_router.post("/{device_id}/restart-bath")
async def restart_bath_app(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启浴室 App：先强停再冷启动。排障用——延长服务流程已改用 power.schedule。"""
    return await device_action(session, device_id, actions.BATH_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


@device_router.post("/{device_id}/reboot")
async def reboot_device(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """重启整机。设备重启后 agent 随之重来，会话与喷头现态由重连后的全貌帧重建。"""
    return await device_action(session, device_id, actions.DEVICE_REBOOT, {}, DEVICE_ACTION_TIMEOUT_S)


# ── 设备界面 ──
# 这一组是"看到并操作设备界面"：dump 给结构（节点树），touch/key/input
# 给动作，与 capture（一张图）互补。排查"设备卡在某个弹窗"时节点树比截图有用。


@device_router.post("/{device_id}/ui-dump")
async def dump_ui_tree(device_id: str, session: Session = Depends(get_db)) -> CommandOutcome:
    """导出设备当前界面的节点树，结果在 data.xml。

    格式与 uiautomator dump 一致；节点上限取配置项 ui_dump_max_nodes。
    """
    return await device_action(session, device_id, actions.UI_DUMP, {}, _ui_timeout_s(session, device_id))


@device_router.post("/{device_id}/ui-touch")
async def touch(device_id: str, req: DeviceUiTouchRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """按屏幕坐标点击或滑动：不给 to_x/to_y 是点击，给了是滑动。

    定位先经 dump 读节点树、由 bounds 求中心点，不使用遍历序号。
    """
    params: dict[str, int] = {"x": req.x, "y": req.y}
    if req.to_x is not None and req.to_y is not None:
        params["to_x"] = req.to_x
        params["to_y"] = req.to_y
    if req.duration_ms is not None:
        params["duration_ms"] = req.duration_ms
    return await device_action(session, device_id, actions.UI_TOUCH, params, _ui_timeout_s(session, device_id))


@device_router.post("/{device_id}/ui-key")
async def send_key(device_id: str, req: DeviceUiKeyRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """发送系统按键（闭集，路由层 422 挡下非法值）。"""
    return await device_action(session, device_id, actions.UI_KEY, {"key": req.key}, _ui_timeout_s(session, device_id))


@device_router.post("/{device_id}/ui-input")
async def input_text(device_id: str, req: DeviceUiTextRequest, session: Session = Depends(get_db)) -> CommandOutcome:
    """向当前焦点控件输入文本；channel 在两条并列通路中显式选择。"""
    return await device_action(
        session,
        device_id,
        actions.UI_INPUT,
        {"text": req.text, "channel": req.channel},
        _ui_timeout_s(session, device_id),
    )


def _require_device(session: Session, device_id: str) -> DeviceRecord:
    """取设备档案；不存在时 404，不用成功响应体里的 found: false 掩盖错误。"""
    device = service.get_device(session, device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return device
