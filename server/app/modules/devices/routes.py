"""设备管理 API：档案维护与远程调试入口。

## 路径规则

集合用复数无动词（`/devices`）；**对单个资源施加动作**用
`<资源>/<动词>`（`/devices/{id}/exec`）。改档案用 `PATCH /devices/{id}`
带字段，而不是为每个字段开一个端点。

## 守卫

整个 ops_router 要求 `X-Ops-Token`；下发命令的端点额外要求设备已信任
且在线（`require_trusted_online_device`）——把命令发给一台离线设备只会白等超时。
"""

from fastapi import APIRouter, Depends

from app.core import actions, events
from app.core.problems import DEVICE_NOT_FOUND, ApiProblemError
from app.core.protocol import as_int
from app.core.security import require_ops_token
from app.modules.config import store as config_store
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import service
from .dependencies import require_trusted_online_device
from .schemas import (
    DeviceExecRequest,
    DevicePatchRequest,
    DeviceRecord,
    DeviceUiKeyRequest,
    DeviceUiScrollRequest,
    DeviceUiTextRequest,
    DeviceUiTouchRequest,
)

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["devices-ops"])

# 设备动作的默认超时：覆盖 root shell 执行、截图编解码这类耗时动作。
DEVICE_ACTION_TIMEOUT_S = 30.0


def _ui_timeout_s(device_id: str) -> float:
    """界面操作的等待上限，取该设备的配置项 `ui_operation_timeout_s`。

    每个 `ui.*` 命令都要等设备完成一次节点遍历或一次手势注入，耗时随界面复杂度
    变化；做成配置项后现场改一次即对全部设备生效，不必改代码重发版。
    """
    timeout_s = as_int(config_store.effective(device_id)["ui_operation_timeout_s"], int(DEVICE_ACTION_TIMEOUT_S))
    return float(timeout_s)


@ops_router.get("/devices")
def list_devices() -> list[DeviceRecord]:
    return service.list_devices()


@ops_router.patch("/devices/{device_id}")
def patch_device(device_id: str, req: DevicePatchRequest) -> DeviceRecord:
    """改设备档案：改名、改信任位。

    只改传了的字段。改名与信任原本各有一个端点（外加一个 `/untrust`），
    但它们做的是同一件事——调整这台设备的档案。

    改完推一条 `device.update`：改的那一端自己会刷新，这里管的是**其它**打开的页面——
    设备下拉里的名字、列表里的信任徽标，不推的话它们要等下一次 30 秒轮询才对得上。
    """
    if req.name is not None:
        service.rename_device(device_id, req.name)
    if req.trusted is not None:
        service.set_trusted(device_id, req.trusted)
    record = _require_device(device_id)
    events.publish(events.DEVICE_UPDATE, dict(record))
    return record


@ops_router.post("/devices/{device_id}/exec")
async def exec_command(device_id: str, req: DeviceExecRequest) -> CommandOutcome:
    """远程调试：在设备上以 root 执行 shell 命令并取回输出（审计留痕）。"""
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.DEVICE_EXEC,
        {"command": req.command},
        timeout_s=DEVICE_ACTION_TIMEOUT_S,
    )


@ops_router.post("/devices/{device_id}/capture")
async def capture_screen(device_id: str) -> CommandOutcome:
    """抓取设备全屏画面，结果在 data.image_base64（JPEG，NO_WRAP）。

    设备端走 screenrecord + MediaCodec 解码首帧——本机 framebuffer/screencap
    取不到画面（硬件合成器直出），实现见 features/remote/ScreenCapture.kt。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.SCREEN_CAPTURE,
        {},
        timeout_s=DEVICE_ACTION_TIMEOUT_S,
    )


@ops_router.post("/devices/{device_id}/restart")
async def restart_agent(device_id: str) -> CommandOutcome:
    """重启设备上的 agent 进程（不重启整机）。"""
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.DEVICE_RESTART,
        {},
        timeout_s=DEVICE_ACTION_TIMEOUT_S,
    )


@ops_router.post("/devices/{device_id}/uninstall")
async def uninstall_agent(device_id: str) -> CommandOutcome:
    """卸载设备上的 agent（用于退役或排障）。"""
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.DEVICE_UNINSTALL,
        {},
        timeout_s=DEVICE_ACTION_TIMEOUT_S,
    )


# ── 设备界面（无障碍服务）──
#
# 这一组是"看到并操作设备界面"的能力，与 capture 的区别是：capture 给的是
# 一张图，这里给的是**结构**（节点树）与**动作**（点击/按键/输入）。
# 排查"设备卡在某个弹窗"时，节点树比截图更有用。


@ops_router.post("/devices/{device_id}/dump")
async def dump_ui_tree(device_id: str) -> CommandOutcome:
    """导出设备当前界面的节点树，结果在 `data.xml`（字符串）。

    格式与 `uiautomator dump` 完全一致：`<hierarchy>` 嵌套 `<node>`，
    `bounds` 写作 `[left,top][right,bottom]`，`index` 是**同级中的序号**。
    节点上限取配置项 `ui_dump_max_nodes`，达到上限即停止遍历。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.UI_DUMP,
        {},
        timeout_s=_ui_timeout_s(device_id),
    )


@ops_router.post("/devices/{device_id}/touch")
async def touch(device_id: str, req: DeviceUiTouchRequest) -> CommandOutcome:
    """按屏幕坐标点击或滑动设备界面。

    坐标由调用方给：先 `/dump` 读节点树，从元素的 `bounds` 算出中心点。
    不给 `to_x`/`to_y` 就是点击，给了就是滑动。
    """
    require_trusted_online_device(device_id)
    params: dict[str, int] = {"x": req.x, "y": req.y}
    if req.to_x is not None and req.to_y is not None:
        params["to_x"] = req.to_x
        params["to_y"] = req.to_y
    if req.duration_ms is not None:
        params["duration_ms"] = req.duration_ms
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.UI_TOUCH,
        params,
        timeout_s=_ui_timeout_s(device_id),
    )


@ops_router.post("/devices/{device_id}/scroll")
async def scroll(device_id: str, req: DeviceUiScrollRequest) -> CommandOutcome:
    """滚动设备的可滚动容器。

    与 `/touch` 的滑动分开：这条作用于**容器**而不是坐标，语义不同——滑动的
    落点取决于起点，滚动只表达"往哪边滚"。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.UI_SCROLL,
        {"direction": req.direction},
        timeout_s=_ui_timeout_s(device_id),
    )


@ops_router.post("/devices/{device_id}/key")
async def send_key(device_id: str, req: DeviceUiKeyRequest) -> CommandOutcome:
    """发送系统按键：back / home / recents / notifications。"""
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.UI_KEY,
        {"key": req.key},
        timeout_s=_ui_timeout_s(device_id),
    )


@ops_router.post("/devices/{device_id}/input")
async def input_text(device_id: str, req: DeviceUiTextRequest) -> CommandOutcome:
    """向设备的当前焦点控件输入文本。

    `channel` 选注入通路：`clipboard`（粘贴，支持中文）或 `keys`（按键注入，仅
    ASCII、无空格问题）。两条是并列通路，见 `DeviceUiTextRequest`。
    """
    require_trusted_online_device(device_id)
    return await sessions_service.send_command_and_wait(
        device_id,
        actions.UI_INPUT,
        {"text": req.text, "channel": req.channel},
        timeout_s=_ui_timeout_s(device_id),
    )


def _require_device(device_id: str) -> DeviceRecord:
    """取设备档案；不存在时 404。

    此前改名/信任接口返回 `{"found": false}`，让调用方自己判断——那等于把
    "设备不存在"这个错误藏在成功响应里。
    """
    device = service.get_device(device_id)
    if device is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id}")
    return device
