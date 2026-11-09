"""ADB 桥接平面：设备二进制 WS 会话、本地回环 TCP 监听与双向字节泵。

安全模型（OWASP WebSocket Cheat Sheet + ADB 无内建认证的现实）：
- 仅管理面（admin 能力位）可触发；
- 每会话一次性 nonce，配对即失效（重放无效）；
- TCP 仅绑定 127.0.0.1（对外经 SSH，认证由 SSH 承担）；
- 只受绝对寿命约束，到点回收；不设空闲超时——设备连入而未敲命令不是"没人用"；
- open/close 全量审计留痕（command_audit 单一来源）。

## 一个建立点、一个回收点

桥只能由 `POST bridge/open` 建立，只能由 `AdbBridge.recycle` 销毁。本文件的其余
部分——设备 WS 端点、字节泵、关闭端点——一律不建桥、不做手工收尾，只调这两个
入口；分散收尾的代价见 bridge/manager.py 的模块注释。
"""

import asyncio
import contextlib
import uuid
from typing import cast

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import actions, outcomes, protocol, reason_codes
from app.core.connections import manager
from app.core.logging import log_event
from app.core.problems import DEVICE_OFFLINE, ApiProblemError
from app.db.base import utcnow
from app.db.deps import get_db
from app.db.session import Database
from app.models.enums import CommandStatus
from app.modules.access.dependencies import require_admin
from app.modules.bridge.schemas import BridgeClosed, BridgeOpened, BridgeStatus
from app.modules.config import store as config_store
from app.modules.devices.dependencies import require_trusted_online_device
from app.persistence import audit as command_audit

from .manager import (
    BRIDGE_BUFFER_SIZE,
    PENDING_MAX,
    AdbBridge,
    registry,
)

router = APIRouter(tags=["bridge"])

# 桥的管理面（开/关/查状态）是网页端调用的 HTTP 端点，随对外 HTTP 面统一挂
# /api/v1；/gateway/bridge 是设备拨入的 WS，归 /gateway/ 硬编码契约，不加前缀。
admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)], tags=["bridge"])

#: 隧道结束时的命令审计标记。它不是设备动作（设备端不执行它），故不进 actions
#: 登记表——那张表与设备端 SupportedActions 对账，只收双端共有的动作名。
SESSION_END_ACTION = "bridge.session_end"


def _bridge_audit_id() -> str:
    """桥审计行的 command_id 构造点（一条桥的生命周期事件共用同一前缀）。"""
    return f"bridge-{uuid.uuid4()}"


def _read_bridge_timeouts(ws: WebSocket) -> tuple[float, float]:
    """设备 WS 握手路径读两个桥超时：应用状态里的 Database 开短会话。

    WS 路径没有请求会话，与 gateway 的 `_db_session` 同一约定；两值一次读齐，
    免得一次握手开两个会话。
    """
    database = cast("Database", ws.app.state.database)
    with database.new_session() as session:
        return (
            _bridge_timeout_s(session, "bridge_max_lifetime_s"),
            _bridge_timeout_s(session, "bridge_pair_timeout_s"),
        )


def _bridge_timeout_s(session: Session, key: str) -> float:
    """读一个桥接超时（秒）。

    三个键都是 SERVER 归属：桥建在服务端侧，取全局有效值即可。不硬编码，调试
    通道该开多久是运维判断的事，项目规范把超时归在必须可调的那一类。
    """
    value = config_store.global_effective(session)[key]
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

    它不建桥。注册表里没有该设备的桥，或 nonce 对不上，都说明这次连接不属于任何
    现役会话（旧世代的残留、重放、或桥已被关），一律拒绝。桥只能由 `bridge/open` 建立。
    """
    device_id = ws.query_params.get("device_id") or ""
    nonce = ws.query_params.get("nonce") or ""
    await ws.accept()

    lifetime_s, pair_s = await run_in_threadpool(_read_bridge_timeouts, ws)
    loop = asyncio.get_running_loop()

    bridge = registry.get(device_id)
    if bridge is None or not bridge.accept_device(ws, nonce, loop.time(), pair_s):
        # 先排水再关：立即 close 会与客户端 close 握手竞态挂死事件循环
        await _drain_and_close(ws, code=4401)
        return

    # 冲缓冲：pending_to_device 是"发往设备"的字节（TCP 先收、设备 WS 未就绪）
    if bridge.pending_to_device:
        try:
            await asyncio.wait_for(ws.send_bytes(bytes(bridge.pending_to_device)), timeout=5.0)
        except (TimeoutError, WebSocketDisconnect, RuntimeError) as exc:
            log_event(
                "WARNING",
                "bridge.flush-failed",
                "桥缓冲发送失败，丢弃本次缓冲",
                device_id=device_id,
                reason=type(exc).__name__,
            )
        bridge.pending_to_device.clear()

    reason = "tunnel closed"
    try:
        while bridge.device_ws is ws:
            remaining = lifetime_s - (loop.time() - bridge.opened_at)
            if remaining <= 0:
                reason = "lifetime reached"
                log_event(
                    "INFO",
                    "bridge.lifetime-reached",
                    "ADB 桥达到最长寿命，开始回收",
                    device_id=device_id,
                    generation=bridge.generation,
                )
                break
            # 这里不设空闲超时：设备侧隧道连入后、本侧尚未 adb connect 之前，一个
            # 字节都不会发，那不是"没人用"而是"还没开始用"。唯一的时间上限是绝对
            # 寿命。
            data = await ws.receive_bytes()
            tcp_writer = bridge.tcp_writer
            if tcp_writer is None:
                _append(bridge.pending_to_tcp, data)
                continue
            tcp_writer.write(data)
            try:
                await asyncio.wait_for(tcp_writer.drain(), timeout=5.0)
            except TimeoutError:
                reason = "tcp write stalled"
                log_event(
                    "WARNING",
                    "bridge.tcp-drain-timeout",
                    "ADB 客户端停止读取，回收桥",
                    device_id=device_id,
                    generation=bridge.generation,
                )
                break
    except WebSocketDisconnect:
        # 设备断开：桥的正常结束路径之一
        reason = "device disconnected"
    finally:
        # 设备 WS 断开意味着通道两端之一消失，整条桥到此为止。recycle 是唯一回收
        # 点，且幂等；device_ws is ws 的判断挡住"新世代已接管"的情况：本协程是旧
        # 世代的残留，收尾不得把新桥一起拆掉。
        if bridge.device_ws is ws:
            await bridge.recycle(reason)
            audit_id = _bridge_audit_id()
            from app.db.session import session_scope

            with session_scope() as session:
                command_audit.insert_command(session, audit_id, device_id, SESSION_END_ACTION, utcnow())
                command_audit.finish(session, audit_id, CommandStatus(outcomes.OK.value), "", reason, utcnow())


async def _pump_tcp_to_ws(bridge: AdbBridge, reader: asyncio.StreamReader) -> None:
    """本地 TCP（adb 客户端侧）→ 设备 WS。

    TCP 客户端断开只释放该连接，桥回到等待下一个 adb 连接的状态；整桥回收由
    设备 WS 断开、bridge/close 或寿命到点触发。这里不设空闲超时：adb 客户端连着
    而未敲命令是常态，桥的绝对寿命已限定总时长；本任务在桥回收时由 detach_client
    取消。退出时只关自己的 TCP 写端，不调 detach_client（自己取消自己会与 WS
    循环的清理相互等待）。
    """
    try:
        while True:
            data = await reader.read(BRIDGE_BUFFER_SIZE)
            if not data:
                break
            device_ws = bridge.device_ws
            if device_ws is None:
                _append(bridge.pending_to_device, data)
                continue
            await device_ws.send_bytes(data)
    except (OSError, WebSocketDisconnect, RuntimeError):
        # 本端流结束：对端断开或设备 WS 已消失。未预料的异常不在此捕获，
        # 应暴露到 asyncio 日志而不是被吞掉。
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
    await _pump_tcp_to_ws(bridge, reader)


@admin_router.post("/devices/{device_id}/bridge/open")
async def adb_open(device_id: str, session: Session = Depends(get_db)) -> BridgeOpened:
    """打开设备的 ADB 桥：下发 bridge.open 并绑定本机回环 TCP 监听。

    整个过程持锁：建立过程有 start_server 与退避重试两处让出事件循环的机会，
    并发的第二个请求会读到半成品状态。若注册表里已有桥则直接返回它，判据是
    "注册表里有它"而非"设备已连入"：桥开着而设备未连入是常见状态（请求刚下发、
    配对超时前），此时建新会话必然撞上自己已绑定的端口。"设备是否连入"由
    bridge/status 的 device_connected 回答。
    """
    require_trusted_online_device(device_id, session)
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
        audit_id = _bridge_audit_id()
        command_audit.insert_command(session, audit_id, device_id, actions.BRIDGE_OPEN, utcnow())
        sent = await manager.send_to(
            device_id,
            protocol.envelope(actions.BRIDGE_OPEN, {"device_id": device_id, "nonce": nonce}),
        )
        if not sent:
            registry.pending.pop(device_id, None)
            command_audit.finish(
                session,
                audit_id,
                CommandStatus(outcomes.TIMEOUT.value),
                reason_codes.DEVICE_OFFLINE.code,
                reason_codes.DEVICE_OFFLINE.message,
                utcnow(),
            )
            raise ApiProblemError(DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")

        # 建桥：绑监听 + 进注册表都在里面完成，失败时不留下任何痕迹
        try:
            bridge = await AdbBridge.open(device_id, generation, port, _serve_client)
        except ApiProblemError as exc:
            registry.pending.pop(device_id, None)
            command_audit.finish(
                session,
                audit_id,
                CommandStatus(outcomes.FAILED.value),
                reason_codes.PORT_BIND_FAILED.code,
                str(exc),
                utcnow(),
            )
            raise
        command_audit.finish(
            session, audit_id, CommandStatus(outcomes.OK.value), "", f"port {port} gen {generation}", utcnow()
        )
        log_event(
            "INFO",
            "bridge.opened",
            "ADB 桥已建立",
            device_id=device_id,
            port=bridge.port,
            generation=bridge.generation,
        )
        # 只返回结构化字段：连接指引属于文档职责（见 docs/runbook.md「5. 故障处置」）
        return {"device_id": device_id, "port": bridge.port, "already_open": False, "generation": bridge.generation}


@admin_router.post("/devices/{device_id}/bridge/close")
async def adb_close(device_id: str, session: Session = Depends(get_db)) -> BridgeClosed:
    """关闭桥：回收监听与流，并让设备停掉 adbd。与开桥共用同一把锁，理由见 adb_open。"""
    async with registry.op_lock:
        bridge = registry.get(device_id)
        audit_id = _bridge_audit_id()
        command_audit.insert_command(session, audit_id, device_id, actions.BRIDGE_CLOSE, utcnow())
        if bridge is None:
            command_audit.finish(session, audit_id, CommandStatus(outcomes.OK.value), "", "already closed", utcnow())
            return {"device_id": device_id, "closed": False}
        await bridge.recycle("requested")
        command_audit.finish(session, audit_id, CommandStatus(outcomes.OK.value), "", "", utcnow())
        # 通知设备停 adbd 的返回值不看：本地已收干净，设备没收到也只是它的 adbd
        # 多开一会儿，下次开桥会重新下发；为一个不影响本地状态的通知改关桥成败
        # 只会让"关没关掉"变成两个问题。
        await manager.send_to(device_id, protocol.envelope(actions.BRIDGE_CLOSE, {}))
        return {"device_id": device_id, "closed": True}


@admin_router.get("/devices/{device_id}/bridge")
async def adb_status(device_id: str, session: Session = Depends(get_db)) -> BridgeStatus:
    """桥接状态可观测（管理面）。

    `expires_in_s` 给出距离绝对寿命到点的秒数——运维据此判断还来不来得及做完
    手上的事，而不是等到连接莫名断开再回头看日志。

    `ssh_target` 与桥开没开无关，所以关着时也给：网页端要靠它拼出本机那条连接命令。
    """
    ssh_target = str(config_store.global_effective(session)["bridge_ssh_target"])
    bridge = registry.get(device_id)
    if bridge is None:
        return {"device_id": device_id, "open": False, "ssh_target": ssh_target}
    lifetime_s = _bridge_timeout_s(session, "bridge_max_lifetime_s")
    remaining = max(0.0, lifetime_s - (asyncio.get_running_loop().time() - bridge.opened_at))
    return {
        "device_id": device_id,
        "open": True,
        "ssh_target": ssh_target,
        "port": bridge.port,
        "generation": bridge.generation,
        "device_connected": bridge.connected,
        "adb_client_connected": bridge.has_adb_client,
        "expires_in_s": round(remaining),
    }
