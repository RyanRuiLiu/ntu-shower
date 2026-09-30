"""ADB 桥接平面：设备二进制 WS 会话、本地回环 TCP 监听与双向字节泵。

安全模型（OWASP WebSocket Cheat Sheet + ADB 无内建认证的现实）：
- 仅管理面（X-Ops-Token）可触发；
- 每会话一次性 nonce，配对即失效（重放无效）；
- 世代令牌：重开时旧世代会话的异步回调全部作废；
- TCP 仅绑定 127.0.0.1（对外经 SSH，认证由 SSH 承担）；
- 空闲 300s 应用层回收（服务器同时跑其他服务，不留死连接）；
- open/close 全量审计留痕（command_audit 单一来源）。
"""

import asyncio
import contextlib
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core import actions, outcomes, protocol, reason_codes
from app.core.problems import DEVICE_OFFLINE, PORT_UNAVAILABLE, ApiProblemError
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
    """设备二进制桥接端点：nonce 配对（世代校验）后，设备 WS 字节 → 本地 TCP。"""
    device_id = ws.query_params.get("device_id") or ""
    nonce = ws.query_params.get("nonce") or ""
    await ws.accept()

    idle_s = _bridge_timeout_s("bridge_idle_timeout_s")
    lifetime_s = _bridge_timeout_s("bridge_max_lifetime_s")
    pair_s = _bridge_timeout_s("bridge_pair_timeout_s")
    loop = asyncio.get_running_loop()
    generation = registry.consume_nonce(device_id, nonce, loop.time(), pair_s)
    if generation is None:
        # 先排水再关：立即 close 会与客户端 close 握手竞态挂死事件循环
        await _drain_and_close(ws, code=4401)
        return

    port = registry.ports.get(device_id, 0)
    bridge = registry.active.get(device_id)
    if bridge is None or bridge.port != port:
        bridge = AdbBridge(device_id, port, generation, opened_at=loop.time())
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
            # 绝对寿命是硬上限：空闲超时只覆盖「没人用」，一直有字节往来时它不会触发，
            # 而调试通道不该无限期开着。取两者中先到的那个，到点即结束循环。
            remaining = lifetime_s - (loop.time() - bridge.opened_at)
            if remaining <= 0:
                logger.info("bridge lifetime reached: device=%s generation=%d", device_id, generation)
                break
            data = await asyncio.wait_for(ws.receive_bytes(), timeout=min(idle_s, remaining))
            tcp_writer = bridge.tcp_writer
            if tcp_writer is None:
                _append(bridge.pending_to_tcp, data)
                continue
            tcp_writer.write(data)
            await tcp_writer.drain()
    except (WebSocketDisconnect, asyncio.TimeoutError):
        # 设备断开、空闲超时或寿命到点：桥的正常结束路径，收尾走 finally
        pass
    finally:
        # 只摘设备 WS 侧；server/TCP 的回收归 adb/close
        if bridge.device_ws is ws and bridge.generation == generation:
            bridge.device_ws = None
            await bridge.close_stream()
            # 监听套接字必须在这里关掉。
            #
            # 只把它从注册表摘掉是不够的——摘掉之后 `POST bridge/close` 就再也找不到这个桥
            # （`registry.active.get` 返回 None，走"本来就没开"分支直接返回），于是端口永久
            # 泄漏。而端口是按设备固定分配的，下次开桥必然 `Errno 98 address already in use`，
            # 表现为"开桥返回 409"，而设备侧此时明明已经连上来了（`/gateway/bridge` 有 accepted
            # 日志）。真实发生过。
            #
            # 用 `close()`（同步，只是停止 accept）而不 `await wait_closed()`：后者要等存活
            # 连接自然结束，而这里正处在 WS 处理协程内，等它会在某些事件循环实现上挂住
            # （Windows proactor）。套接字本身由 asyncio 的关闭回调回收。
            #
            # 判据用 `is_serving()`：`close()` 不是幂等的，重复调用会撞上内部已清空的成员。
            if bridge.server is not None and bridge.server.is_serving():
                bridge.server.close()
            bridge.server = None
            registry.remove(device_id)
            audit_id = f"bridge-{uuid.uuid4()}"
            command_audit.insert_command(audit_id, device_id, "bridge.session_end")
            command_audit.finish(audit_id, outcomes.OK.value, "", "")


async def _pump_tcp_to_ws(bridge: AdbBridge, reader: asyncio.StreamReader, idle_s: float) -> None:
    """本地 TCP（adb 客户端侧）→ 设备 WS。

    TCP 客户端断开只释放该连接（回到"等待下一个 adb 连接"，对齐旧项目隧道语义）；
    整桥回收由设备 WS 断开、'adb/close' 或超时触发。
    泵退出只关自己的 TCP 写端——绝不在 finally 里调 close_stream（自己取消自己
    会与 WS 循环的清理形成相互等待，真机上表现为桥挂死不释放）。
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


@ops_router.post("/devices/{device_id}/bridge/open")
async def adb_open(device_id: str) -> BridgeOpened:
    """打开设备的 ADB 桥：下发 bridge.open 并绑定本机回环 TCP 监听。"""
    require_trusted_online_device(device_id)
    existing = registry.active.get(device_id)
    if existing is not None and existing.connected:
        # 与下方成功分支保持同一字段集：客户端只需处理一种响应形状
        return {"device_id": device_id, "port": existing.port, "already_open": True, "generation": existing.generation}

    idle_s = _bridge_timeout_s("bridge_idle_timeout_s")
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
            audit_id, outcomes.TIMEOUT.value, reason_codes.DEVICE_OFFLINE.code, reason_codes.DEVICE_OFFLINE.message
        )
        raise ApiProblemError(DEVICE_OFFLINE, f"设备 {device_id} 没有活跃连接")
    command_audit.finish(audit_id, outcomes.OK.value, "", f"port {port} gen {generation}")

    bridge = AdbBridge(device_id, port, generation, opened_at=loop.time())
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
        await _pump_tcp_to_ws(bridge, reader, idle_s)

    server = None
    last_error: OSError | None = None
    # 关闭后立刻重开有内核端口释放竞态。
    #
    # 端口是**按设备固定分配**的（运维的 adb connect 脚本因此可以写死，见 manager 的
    # open_session），所以每次重开必然撞上同一个端口。而上一个监听关闭后，它的连接会
    # 在 TIME_WAIT 里待一会儿，`SO_REUSEADDR` 也压不住——实测关桥后立刻再开必定
    # `Errno 98`，而原定的三次退避总共只有 1.2 秒，不够。
    #
    # 放到五次、退避 0.3 递增（合计约 4.5 秒）。重开桥是罕见操作，等几秒可以接受；
    # 而失败一次的代价是运维得手动重试，还会看到"设备明明连上了却开不了桥"这种怪现象。
    #
    # 上限压在 5 秒以内是刻意的：那是本项目的单例超时约定（见 AGENTS.md），超过它说明
    # 这段代码在等一个不会到来的事件。再长就该去查端口为什么没放，而不是继续等。
    for attempt in range(5):
        try:
            server = await asyncio.start_server(handle_client, "127.0.0.1", port)
            break
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.3 * (attempt + 1))
    if server is None:
        registry.pending.pop(device_id, None)
        registry.remove(device_id)
        command_audit.finish(audit_id, outcomes.FAILED.value, "port.bind_failed", str(last_error))
        logger.warning("bridge listen failed: device=%s port=%d error=%s", device_id, port, last_error)
        raise ApiProblemError(PORT_UNAVAILABLE, f"端口 {port} 被占用：{last_error}")
    bridge.server = server
    logger.info("bridge opened: device=%s port=%d generation=%d", device_id, port, generation)
    # 只返回结构化字段：连接指引属于文档职责（见 docs/RUNBOOK.md §4）
    return {"device_id": device_id, "port": port, "already_open": False, "generation": generation}


@ops_router.post("/devices/{device_id}/bridge/close")
async def adb_close(device_id: str) -> BridgeClosed:
    bridge = registry.active.get(device_id)
    audit_id = f"bridge-{uuid.uuid4()}"
    command_audit.insert_command(audit_id, device_id, actions.BRIDGE_CLOSE)
    if bridge is None:
        command_audit.finish(audit_id, outcomes.OK.value, "", "already closed")
        return {"device_id": device_id, "closed": False}
    await bridge.close()
    registry.remove(device_id)
    command_audit.finish(audit_id, outcomes.OK.value, "", "")
    await manager.send_to(device_id, protocol.envelope(actions.BRIDGE_CLOSE, {}))
    return {"device_id": device_id, "closed": True}


@ops_router.get("/devices/{device_id}/bridge/status")
async def adb_status(device_id: str) -> BridgeStatus:
    """桥接状态可观测（管理面）。

    `expires_in_s` 给出距离**绝对寿命**到点的秒数——运维据此判断还来不来得及做完
    手上的事，而不是等到连接莫名断开再回头看日志。
    """
    bridge = registry.active.get(device_id)
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
