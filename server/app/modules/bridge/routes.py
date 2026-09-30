"""ADB 桥接平面：设备二进制 WS 会话、本地回环 TCP 监听与双向字节泵。

安全模型（OWASP WebSocket Cheat Sheet + ADB 无内建认证的现实）：
- 仅管理面（X-Admin-Token）可触发；
- 每会话一次性 nonce，配对即失效（重放无效）；
- 世代令牌：重开时旧世代会话的异步回调全部作废；
- TCP 仅绑定 127.0.0.1（对外经 SSH，认证由 SSH 承担）；
- 空闲 300s 应用层回收（服务器同时跑其他服务，不留死连接）；
- open/close 全量审计留痕（command_audit 单一来源）。
"""

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core import protocol
from app.core.problems import DEVICE_OFFLINE, PORT_UNAVAILABLE, ApiProblemError
from app.core.protocol import JsonObject
from app.core.security import require_admin
from app.gateway.manager import manager
from app.modules.devices.dependencies import require_trusted_online_device
from app.modules.sessions import repository as command_audit

from .manager import (
    BRIDGE_BUFFER_SIZE,
    BRIDGE_IDLE_TIMEOUT_S,
    PENDING_MAX,
    AdbBridge,
    registry,
)

router = APIRouter(tags=["bridge"])

admin_router = APIRouter(prefix="/admin", tags=["bridge-admin"], dependencies=[Depends(require_admin)])


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
    """设备二进制桥接端点：nonce 配对（世代校验）后，设备 WS 字节 → 本地 TCP。"""
    device_id = ws.query_params.get("device_id") or ""
    nonce = ws.query_params.get("nonce") or ""
    await ws.accept()

    generation = registry.consume_nonce(device_id, nonce)
    if generation is None:
        # 先排水再关：立即 close 会与客户端 close 握手竞态挂死事件循环
        await _drain_and_close(ws, code=4401)
        return

    port = registry.ports.get(device_id, 0)
    bridge = registry.active.get(device_id)
    if bridge is None or bridge.port != port:
        bridge = AdbBridge(device_id, port, generation)
        registry.active[device_id] = bridge
    elif bridge.generation > generation:
        # 旧世代重放：已有更新的世代在册，拒绝本帧（不影响现役会话）。
        # 判定必须是严格大于——相等表示本次 open 刚建立的桥正等待设备接管。
        await _drain_and_close(ws, code=4409)
        return
    else:
        # 新世代接管：拆掉旧会话的流（幂等），保留监听
        await bridge.close_stream()
        bridge.generation = generation
        bridge.device_ws = ws

    bridge.device_ws = ws

    # 冲缓冲：pending_to_device 是"发往设备"的字节（TCP 先收、设备 WS 未就绪）
    if bridge.pending_to_device:
        with contextlib.suppress(Exception):
            await ws.send_bytes(bytes(bridge.pending_to_device))
        bridge.pending_to_device.clear()

    try:
        while bridge.device_ws is ws and bridge.generation == generation:
            data = await asyncio.wait_for(ws.receive_bytes(), timeout=BRIDGE_IDLE_TIMEOUT_S)
            tcp_writer = bridge.tcp_writer
            if tcp_writer is None:
                _append(bridge.pending_to_tcp, data)
                continue
            tcp_writer.write(data)
            await tcp_writer.drain()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        # 设备断开或空闲超时：桥的正常结束路径，收尾走 finally
        pass
    finally:
        # 只摘设备 WS 侧；server/TCP 的回收归 adb/close（避免与门户循环关闭竞态，
        # Windows proactor 上在 WS 处理协程内同步关 server 会挂住事件循环）
        if bridge.device_ws is ws and bridge.generation == generation:
            bridge.device_ws = None
            await bridge.close_stream()
            registry.remove(device_id)
            audit_id = f"bridge-{uuid.uuid4()}"
            command_audit.insert_command(audit_id, device_id, "adb.session_end")
            command_audit.finish(audit_id, "ok", "", "")


async def _pump_tcp_to_ws(bridge: AdbBridge, reader: asyncio.StreamReader) -> None:
    """本地 TCP（adb 客户端侧）→ 设备 WS。

    TCP 客户端断开只释放该连接（回到"等待下一个 adb 连接"，对齐旧项目隧道语义）；
    整桥回收由设备 WS 断开、adb/close 或空闲超时触发。
    泵退出只关自己的 TCP 写端——绝不在 finally 里调 close_stream（自己取消自己
    会与 WS 循环的清理形成相互等待，真机上表现为桥挂死不释放）。
    """
    try:
        while True:
            data = await asyncio.wait_for(reader.read(BRIDGE_BUFFER_SIZE), timeout=BRIDGE_IDLE_TIMEOUT_S)
            if not data:
                break
            device_ws = bridge.device_ws
            if device_ws is None:
                _append(bridge.pending_to_device, data)
                continue
            await device_ws.send_bytes(data)
    except (OSError, asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        # 本端流结束：对端断开、空闲超时或设备 WS 已消失；收尾交 finally。
        # 刻意不捕获 Exception——未预料的异常应暴露到 asyncio 日志而不是被吞掉。
        pass
    finally:
        writer = bridge.tcp_writer
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
        bridge.tcp_writer = None
        bridge.tcp_reader = None


@admin_router.post("/devices/{device_id}/adb/open")
async def adb_open(device_id: str) -> JsonObject:
    """打开设备的 ADB 桥：下发 bridge.open 并绑定本机回环 TCP 监听。"""
    require_trusted_online_device(device_id)
    existing = registry.active.get(device_id)
    if existing is not None and existing.connected:
        # 与下方成功分支保持同一字段集：客户端只需处理一种响应形状
        return {"device_id": device_id, "port": existing.port, "already_open": True, "generation": existing.generation}

    generation, port, nonce = registry.open_session(device_id)
    audit_id = f"bridge-{uuid.uuid4()}"
    command_audit.insert_command(audit_id, device_id, "adb.open")
    sent = await manager.send_to(
        device_id,
        protocol.envelope("bridge.open", {"device_id": device_id, "nonce": nonce}),
    )
    if not sent:
        registry.pending.pop(device_id, None)
        command_audit.finish(audit_id, "timeout", "device.offline", "device offline")
        raise ApiProblemError(DEVICE_OFFLINE, f"device {device_id} has no active connection")
    command_audit.finish(audit_id, "ok", "", f"port {port} gen {generation}")

    bridge = AdbBridge(device_id, port, generation)
    registry.active[device_id] = bridge

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 一个世代同一时刻只服务一个 adb 客户端：并发连接直接拒绝
        if bridge.has_adb_client:
            writer.close()
            return
        bridge.tcp_reader = reader
        bridge.tcp_writer = writer
        bridge.pump_task = asyncio.current_task()
        # 冲缓冲：设备 WS 先于 TCP 到达的字节
        if bridge.pending_to_tcp:
            writer.write(bytes(bridge.pending_to_tcp))
            await writer.drain()
            bridge.pending_to_tcp.clear()
        await _pump_tcp_to_ws(bridge, reader)

    server = None
    last_error: OSError | None = None
    for attempt in range(3):  # 关闭后立刻重开有内核端口释放竞态，退避重试
        try:
            server = await asyncio.start_server(handle_client, "127.0.0.1", port)
            break
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.2 * (attempt + 1))
    if server is None:
        registry.pending.pop(device_id, None)
        registry.remove(device_id)
        command_audit.finish(audit_id, "failed", "port.bind_failed", str(last_error))
        raise ApiProblemError(PORT_UNAVAILABLE, f"port {port}: {last_error}")
    bridge.server = server
    # 只返回结构化字段：连接指引属于文档职责（见 docs/RUNBOOK.md §4）
    return {"device_id": device_id, "port": port, "already_open": False, "generation": generation}


@admin_router.post("/devices/{device_id}/adb/close")
async def adb_close(device_id: str) -> JsonObject:
    bridge = registry.active.get(device_id)
    audit_id = f"bridge-{uuid.uuid4()}"
    command_audit.insert_command(audit_id, device_id, "adb.close")
    if bridge is None:
        command_audit.finish(audit_id, "ok", "", "already closed")
        return {"device_id": device_id, "closed": False}
    await bridge.close()
    registry.remove(device_id)
    command_audit.finish(audit_id, "ok", "", "")
    await manager.send_to(device_id, protocol.envelope("bridge.close", {}))
    return {"device_id": device_id, "closed": True}


@admin_router.get("/devices/{device_id}/adb/status")
async def adb_status(device_id: str) -> JsonObject:
    """桥接状态可观测（管理面）。"""
    bridge = registry.active.get(device_id)
    if bridge is None:
        return {"device_id": device_id, "open": False}
    return {
        "device_id": device_id,
        "open": True,
        "port": bridge.port,
        "generation": bridge.generation,
        "device_connected": bridge.connected,
        "adb_client_connected": bridge.has_adb_client,
    }
