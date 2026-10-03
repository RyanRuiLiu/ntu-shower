"""设备域 API：精简清单（公开）、运维档案与远程操作（admin）。

## 路径规则

资源用复数名词；对单个资源施加动作用 `<资源>/{id}/<动词>`；改档案用 PATCH
带字段。目标设备一律在路径里，请求体只放动作参数。

## 两种表示，各自一个端点

- `GET /devices`：精简视图（id、名称、在线、actions）——查看语义，公开；
  `actions` 由 access.user_actions 按调用方能力位与作用域算出，客户端不复制
  判定规则。
- `GET /devices/runtime`：运维档案（版本、信任、IP、时钟偏差等诊断字段），
  admin 专属，管理台设备表专用。

## 守卫

admin 端点统一挂 require_admin；下发命令的端点额外要求设备已信任且在线
（require_trusted_online_device）——把命令发给离线设备只会白等超时。
"""

from fastapi import APIRouter, Depends

from app.core import actions
from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.core.protocol import JsonObject, as_int
from app.modules.access.capabilities import user_actions
from app.modules.access.dependencies import optional_identity, require_admin
from app.modules.access.schemas import IdentityRecord
from app.modules.config import store as config_store
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import service
from .dependencies import require_trusted_online_device
from .schemas import (
    DeviceClockSetRequest,
    DeviceExecRequest,
    DeviceListItem,
    DevicePatchRequest,
    DeviceRecord,
    DeviceUiKeyRequest,
    DeviceUiScrollRequest,
    DeviceUiTextRequest,
    DeviceUiTouchRequest,
)

router = APIRouter(prefix="/api/v1", tags=["devices"])
admin_router = APIRouter(prefix="/api/v1/devices", dependencies=[Depends(require_admin)], tags=["devices-admin"])

# 设备动作的默认超时：覆盖 root shell 执行、截图编解码这类耗时动作。
DEVICE_ACTION_TIMEOUT_S = 30.0


def _ui_timeout_s(device_id: str) -> float:
    """界面操作的等待上限，取该设备的配置项 `ui_operation_timeout_s`。

    每个 `ui.*` 命令都要等设备完成一次节点遍历或一次手势注入，耗时随界面复杂度
    变化；做成配置项后现场改一次即对全部设备生效。
    """
    timeout_s = as_int(config_store.effective(device_id)["ui_operation_timeout_s"], int(DEVICE_ACTION_TIMEOUT_S))
    return float(timeout_s)


async def _device_action(device_id: str, action: str, params: JsonObject, timeout_s: float) -> CommandOutcome:
    """远程动作的公共路径：设备须已信任在线，命令经统一下发与等待取回结果。

    全部 admin 动作端点共用——差别只在动作名、参数与超时，守卫与下发只有这一份。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(device_id, action, params, timeout_s=timeout_s)


# ── 查询 ──


@router.get("/devices")
def list_devices(caller: IdentityRecord | None = Depends(optional_identity)) -> list[DeviceListItem]:
    """精简设备清单：id、名称、在线，以及该调用方对每台可执行的用户动作。

    匿名调用 actions 恒为空；判定经 access 的唯一实现，客户端不复制作用域规则。
    """
    items: list[DeviceListItem] = []
    for record in service.list_devices():
        in_scope = caller is not None and (
            "*" in caller["device_scope"] or record["device_id"] in caller["device_scope"]
        )
        items.append(
            {
                **service.to_summary(record),
                "actions": user_actions(caller["capabilities"]) if in_scope else [],
            }
        )
    return items


@admin_router.get("/runtime")
def list_runtime() -> list[DeviceRecord]:
    """全部设备的运维档案（版本、信任、来源 IP、时钟偏差等诊断字段）。"""
    return service.list_devices()


# ── 档案 ──


@admin_router.patch("/{device_id}")
def patch_device(device_id: str, req: DevicePatchRequest) -> DeviceRecord:
    """改设备档案：改名、改信任位，只改传了的字段。

    改完推一条 `device.update`（精简载荷）：其它打开的页面要靠它更新设备名与
    在线标记，不推就要等轮询。
    """
    if req.name is not None:
        service.rename_device(device_id, req.name)
    if req.trusted is not None:
        service.set_trusted(device_id, req.trusted)
    record = _require_device(device_id)
    service.publish_device_update(device_id)
    return record


# ── 远程动作 ──


@admin_router.post("/{device_id}/exec")
async def exec_command(device_id: str, req: DeviceExecRequest) -> CommandOutcome:
    """远程调试：在设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    return await _device_action(device_id, actions.DEVICE_EXEC, {"command": req.command}, DEVICE_ACTION_TIMEOUT_S)


@admin_router.post("/{device_id}/capture")
async def capture_screen(device_id: str) -> CommandOutcome:
    """抓取设备全屏画面，结果在 data.image_base64（JPEG，NO_WRAP）。

    设备端走 screenrecord + MediaCodec 解码首帧：本机 framebuffer 与 screencap
    取不到画面（硬件合成器直出）。
    """
    return await _device_action(device_id, actions.SCREEN_CAPTURE, {}, DEVICE_ACTION_TIMEOUT_S)


@admin_router.post("/{device_id}/restart")
async def restart_agent(device_id: str) -> CommandOutcome:
    """重启设备上的 agent 进程（不重启整机）。"""
    return await _device_action(device_id, actions.DEVICE_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


@admin_router.post("/{device_id}/uninstall")
async def uninstall_agent(device_id: str) -> CommandOutcome:
    """卸载设备上的 agent（退役或排障用）。"""
    return await _device_action(device_id, actions.DEVICE_UNINSTALL, {}, DEVICE_ACTION_TIMEOUT_S)


@admin_router.post("/{device_id}/clock/set")
async def set_clock(device_id: str, req: DeviceClockSetRequest) -> CommandOutcome:
    """设置设备系统时钟（datetime 为设备本地墙钟时间）。浴室 App 的每日开关机计划依赖它。"""
    return await _device_action(device_id, actions.CLOCK_SET, {"datetime": req.datetime}, DEVICE_ACTION_TIMEOUT_S)


@admin_router.post("/{device_id}/bath/restart")
async def restart_bath_app(device_id: str) -> CommandOutcome:
    """重启浴室 App：先强停再冷启动，让它重读每日定时开关机计划。"""
    return await _device_action(device_id, actions.BATH_RESTART, {}, DEVICE_ACTION_TIMEOUT_S)


# ── 设备界面 ──
# 这一组是"看到并操作设备界面"：dump 给结构（节点树），touch/scroll/key/input
# 给动作，与 capture（一张图）互补。排查"设备卡在某个弹窗"时节点树比截图有用。


@admin_router.post("/{device_id}/dump")
async def dump_ui_tree(device_id: str) -> CommandOutcome:
    """导出设备当前界面的节点树，结果在 data.xml。

    格式与 uiautomator dump 一致；节点上限取配置项 ui_dump_max_nodes。
    """
    return await _device_action(device_id, actions.UI_DUMP, {}, _ui_timeout_s(device_id))


@admin_router.post("/{device_id}/touch")
async def touch(device_id: str, req: DeviceUiTouchRequest) -> CommandOutcome:
    """按屏幕坐标点击或滑动：不给 to_x/to_y 是点击，给了是滑动。

    定位先经 dump 读节点树、由 bounds 求中心点，不使用遍历序号。
    """
    params: dict[str, int] = {"x": req.x, "y": req.y}
    if req.to_x is not None and req.to_y is not None:
        params["to_x"] = req.to_x
        params["to_y"] = req.to_y
    if req.duration_ms is not None:
        params["duration_ms"] = req.duration_ms
    return await _device_action(device_id, actions.UI_TOUCH, params, _ui_timeout_s(device_id))


@admin_router.post("/{device_id}/scroll")
async def scroll(device_id: str, req: DeviceUiScrollRequest) -> CommandOutcome:
    """滚动可滚动容器。与 touch 的滑动分开：作用于容器而非坐标。"""
    return await _device_action(device_id, actions.UI_SCROLL, {"direction": req.direction}, _ui_timeout_s(device_id))


@admin_router.post("/{device_id}/key")
async def send_key(device_id: str, req: DeviceUiKeyRequest) -> CommandOutcome:
    """发送系统按键（闭集，路由层 422 挡下非法值）。"""
    return await _device_action(device_id, actions.UI_KEY, {"key": req.key}, _ui_timeout_s(device_id))


@admin_router.post("/{device_id}/input")
async def input_text(device_id: str, req: DeviceUiTextRequest) -> CommandOutcome:
    """向当前焦点控件输入文本；channel 在两条并列通路中显式选择。"""
    return await _device_action(
        device_id, actions.UI_INPUT, {"text": req.text, "channel": req.channel}, _ui_timeout_s(device_id)
    )


def _require_device(device_id: str) -> DeviceRecord:
    """取设备档案；不存在时 404，不用成功响应体里的 found: false 掩盖错误。"""
    device = service.get_device(device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return device
