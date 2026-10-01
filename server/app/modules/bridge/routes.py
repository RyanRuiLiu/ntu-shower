"""ADB 桥接平面：设备二进制 WS 会话、本地回环 TCP 监听与双向字节泵。

安全模型（OWASP WebSocket Cheat Sheet + ADB 无内建认证的现实）：
- 仅管理面（X-Ops-Token）可触发；
- 每会话一次性 nonce，配对即失效（重放无效）；
- TCP 仅绑定 127.0.0.1（对外经 SSH，认证由 SSH 承担）；
- 空闲 300s 应用层回收（服务器同时跑其他服务，不留死连接）；
- open/close 全量审计留痕（command_audit 单一来源）。

## 一个建立点、一个回收点

桥只能由 `POST bridge/open` 建立（`AdbBridge.open`），只能由 `AdbBridge.recycle` 销毁。
本文件的其余部分——设备 WS 端点、字节泵、关闭端点——**一律不建桥、不做手工收尾**，
它们只调这两个入口。

这条约束是刻意的。此前四处各自收尾：设备 WS 端点自己建桥、自己关监听并摘注册表；
关闭端点又关一次、又摘一次；字节泵再清一遍 TCP 字段。四份实现互不知情，于是每修一个
bug 都要改多处，漏掉一处就是端口泄漏——真实发生过，表现为之后每次开桥都 `Errno 98`，
而设备侧明明已经连上来。
"""

import asyncio
import contextlib
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core import actions, outcomes, protocol, reason_codes
from app.core.problems import DEVICE_OFFLINE, ApiProblemError
from app.core.security import require_ops_token
from app.gateway.manager import manager
from app.modules.bridge.schemas import BridgeClosed, BridgeOpened, BridgeStatus
from app.modules.config import store as config_store
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import repository as command_audit

from .manager import (
    BRIDGE_BUFFER_SIZE,
    PENDING_MAX,
    AdbBridge,
    registry,
)

logger = logging.getLogger("ntu-shower.bridge")

router = APIRouter(tags=["bridge"])

ops_router = APIRouter(prefix="/ops", tags=["bridge-ops"], dependencies=[Depends(require_ops_token)])


def _bridge_timeout_s(key: str) -> float:
    """读一个桥接超时（秒）。

    三个键都是 SERVER 归属——桥建在服务端侧，取全局有效值即可。**不硬编码**：
    调试通道该开多久是运维判断的事，而项目规范把超时归在必须可调的那一类。
    """
    value = config_store.global_effective()[key]
    return float(value) if isinstance(value, int) else 0.0


def _append(buffer: bytearray, data: bytes) -> None:
    """有界缓冲：对端未就绪时暂存字节，超出上限丢弃最旧数据。"""
    buffer.extend(data)
    if len(buffer) > PENDING_MAX:
        del buffer[: len(buffer) - PENDING_MAX]


async def _drain_and_close(ws: WebSocket, code: int) -> None:
    """拒绝路径的规范关闭：服务端先发 close，再排掉客户端回执帧，避免握手竞态。

    客户端可能不回执（或已消失），排水失败不影响关闭语义，故整体忽略。
    """
    await ws.close(code=code)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(ws.receive(), timeout=2)


@router.websocket("/gateway/bridge")
async def device_bridge(ws: WebSocket) -> None:
    """设备二进制桥接端点：接管一条已建立的桥，然后双向搬运字节。

    **它不建桥。** 注册表里没有该设备的桥，或 nonce 对不上，都说明这次连接不属于任何
    现役会话（旧世代的残留、重放、或桥已被关），一律拒绝。桥只能由 `bridge/open` 建立。
    """
    device_id = ws.query_params.get("device_id") or ""
    nonce = ws.query_params.get("nonce") or ""
    await ws.accept()

    idle_s = _bridge_timeout_s("bridge_idle_timeout_s")
    lifetime_s = _bridge_timeout_s("bridge_max_lifetime_s")
    pair_s = _bridge_timeout_s("bridge_pair_timeout_s")
    loop = asyncio.get_running_loop()

    bridge = registry.get(device_id)
    if bridge is None or not bridge.accept_device(ws, nonce, loop.time(), pair_s):
        # 先排水再关：立即 close 会与客户端 close 握手竞态挂死事件循环
        await _drain_and_close(ws, code=4401)
        return

    # 冲缓冲：pending_to_device 是"发往设备"的字节（TCP 先收、设备 WS 未就绪）
    if bridge.pending_to_device:
        with contextlib.suppress(Exception):
            await ws.send_bytes(bytes(bridge.pending_to_device))
        bridge.pending_to_device.clear()

    try:
        while bridge.device_ws is ws:
            # 绝对寿命是硬上限：空闲超时只覆盖「没人用」，一直有字节往来时它不会触发，
            # 而调试通道不该无限期开着。取两者中先到的那个，到点即结束循环。
            remaining = lifetime_s - (loop.time() - bridge.opened_at)
            if remaining <= 0:
                logger.info("bridge lifetime reached: device=%s generation=%d", device_id, bridge.generation)
                break
            data = await asyncio.wait_for(ws.receive_bytes(), timeout=min(idle_s, remaining))
            tcp_writer = bridge.tcp_writer
            if tcp_writer is None:
                _append(bridge.pending_to_tcp, data)
                continue
            tcp_writer.write(data)
            await tcp_writer.drain()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        # 设备断开、空闲超时或寿命到点：桥的正常结束路径
        pass
    finally:
        # 设备 WS 断了就意味着这条通道的两端之一消失，整条桥到此为止。
        #
        # recycle() 是唯一回收点，它负责关监听、断 TCP 流、摘注册表，且幂等。
        # `device_ws is ws` 那层判断挡的是"新世代已经接管"的情况：那时本协程是旧世代的
        # 残留，收尾会把新桥一起拆掉。
        if bridge.device_ws is ws:
            await bridge.recycle()
            audit_id = f"bridge-{uuid.uuid4()}"
            command_audit.insert_command(audit_id, device_id, "bridge.session_end")
            command_audit.finish(audit_id, outcomes.OK.value, "", "")


async def _pump_tcp_to_ws(bridge: AdbBridge, reader: asyncio.StreamReader, idle_s: float) -> None:
    """本地 TCP（adb 客户端侧）→ 设备 WS。

    TCP 客户端断开只释放该连接（回到"等待下一个 adb 连接"，对齐旧系统隧道语义）；
    整桥回收由设备 WS 断开、'POST bridge/close' 或超时触发。

    泵退出时只关自己的 TCP 写端，**不调 `detach_client()`**：那会 cancel 当前任务，
    而自己取消自己会与 WS 循环的清理形成相互等待——真机上表现为桥挂死不释放。
    """
    try:
        while True:
            data = await asyncio.wait_for(reader.read(BRIDGE_BUFFER_SIZE), timeout=idle_s)
            if not data:
                break
            device_ws = bridge.device_ws
            if device_ws is None:
                _append(bridge.pending_to_device, data)
                continue
            await device_ws.send_bytes(data)
    except (OSError, asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        # 本端流结束：对端断开、空闲超时或设备 WS 已消失。
        # 刻意不捕获 Exception——未预料的异常应暴露到 asyncio 日志而不是被吞掉。
        pass
    finally:
        bridge.pump_task = None
        if bridge.tcp_writer is not None:
            with contextlib.suppress(Exception):
                bridge.tcp_writer.close()
        bridge.tcp_writer = None
        bridge.tcp_reader = None


async def _serve_client(bridge: AdbBridge, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """一个 adb 客户端接入时的入口（由 `AdbBridge.open` 绑给监听套接字）。"""
    if not bridge.attach_client(reader, writer):
        # 一个世代同一时刻只服务一个 adb 客户端：并发连接直接拒绝（表现为连上即 EOF）
        writer.close()
        return
    bridge.pump_task = asyncio.current_task()
    # 冲缓冲：设备 WS 先于 TCP 到达的字节
    if bridge.pending_to_tcp:
        writer.write(bytes(bridge.pending_to_tcp))
        await writer.drain()
        bridge.pending_to_tcp.clear()
    await _pump_tcp_to_ws(bridge, reader, _bridge_timeout_s("bridge_idle_timeout_s"))


@ops_router.post("/devices/{device_id}/bridge/open")
async def adb_open(device_id: str) -> BridgeOpened:
    """打开设备的 ADB 桥：下发 bridge.open 并绑定本机回环 TCP 监听。

    整个过程持锁：建立过程里有 `start_server` 与退避重试两次让出事件循环的机会，
    并发进来的第二个请求会读到半成品状态，然后去建新会话、撞端口。

    若注册表里已经有桥，直接返回它——**判据是"注册表里有它"，不看设备连没连上**。
    桥开着而设备还没连入是常见状态（请求刚下发、设备正重连、配对超时前的那 30 秒），
    此时若去建新会话，端口必然撞上自己已经绑好的那个。实测因此一路 409，而设备侧
    明明已经连上来。"设备没连入"由 GET bridge/status 的 device_connected 回答，
    不该塞进开桥这个动作的成败里。
    """
    require_trusted_online_device(device_id)
    async with registry.op_lock:
        existing = registry.get(device_id)
        if existing is not None:
            return {
                "device_id": device_id,
                "port": existing.port,
                "already_open": True,
                "generation": existing.generation,
            }

        loop = asyncio.get_running_loop()
        generation, port, nonce = registry.open_session(device_id, loop.time())
        audit_id = f"bridge-{uuid.uuid4()}"
        command_audit.insert_command(audit_id, device_id, actions.BRIDGE_OPEN)
        sent = await manager.send_to(
            device_id,
            protocol.envelope(actions.BRIDGE_OPEN, {"device_id": device_id, "nonce": nonce}),
        )
        if not sent:
            registry.pending.pop(device_id, None)
            command_audit.finish(
                audit_id,
                outcomes.TIMEOUT.value,
                reason_codes.DEVICE_OFFLINE.code,
                reason_codes.DEVICE_OFFLINE.message,
            )
            raise ApiProblemError(DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")

        # 建桥：绑监听 + 进注册表都在里面完成，失败时不留下任何痕迹
        try:
            bridge = await AdbBridge.open(device_id, generation, port, _serve_client)
        except ApiProblemError as exc:
            registry.pending.pop(device_id, None)
            command_audit.finish(audit_id, outcomes.FAILED.value, "port.bind_failed", str(exc))
            raise
        command_audit.finish(audit_id, outcomes.OK.value, "", f"port {port} gen {generation}")
        logger.info("bridge opened: device=%s port=%d generation=%d", device_id, bridge.port, bridge.generation)
        # 只返回结构化字段：连接指引属于文档职责（见 docs/RUNBOOK.md §4）
        return {"device_id": device_id, "port": bridge.port, "already_open": False, "generation": bridge.generation}


@ops_router.post("/devices/{device_id}/bridge/close")
async def adb_close(device_id: str) -> BridgeClosed:
    """关闭桥：回收监听与流，并让设备停掉 adbd。与开桥共用同一把锁，理由见 adb_open。"""
    async with registry.op_lock:
        bridge = registry.get(device_id)
        audit_id = f"bridge-{uuid.uuid4()}"
        command_audit.insert_command(audit_id, device_id, actions.BRIDGE_CLOSE)
        if bridge is None:
            command_audit.finish(audit_id, outcomes.OK.value, "", "already closed")
            return {"device_id": device_id, "closed": False}
        await bridge.recycle()
        command_audit.finish(audit_id, outcomes.OK.value, "", "")
        await manager.send_to(device_id, protocol.envelope(actions.BRIDGE_CLOSE, {}))
        return {"device_id": device_id, "closed": True}


@ops_router.get("/devices/{device_id}/bridge/status")
async def adb_status(device_id: str) -> BridgeStatus:
    """桥接状态可观测（管理面）。

    `expires_in_s` 给出距离**绝对寿命**到点的秒数——运维据此判断还来不来得及做完
    手上的事，而不是等到连接莫名断开再回头看日志。
    """
    bridge = registry.get(device_id)
    if bridge is None:
        return {"device_id": device_id, "open": False}
    lifetime_s = _bridge_timeout_s("bridge_max_lifetime_s")
    remaining = max(0.0, lifetime_s - (asyncio.get_running_loop().time() - bridge.opened_at))
    return {
        "device_id": device_id,
        "open": True,
        "port": bridge.port,
        "generation": bridge.generation,
        "device_connected": bridge.connected,
        "adb_client_connected": bridge.has_adb_client,
        "expires_in_s": round(remaining),
    }
