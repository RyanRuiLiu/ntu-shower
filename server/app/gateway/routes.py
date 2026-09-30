"""设备 WS 网关平面：/gateway/device。

帧处理规则：
- 第一帧必须是 link.hello，否则报错断开；
- link.ping → link.pong（心跳回路）；
- 未知 type → link.error(proto.unknown_type)，不断开（协议扩展点）；
- 未信任设备保持连接但不进入任何业务。
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..core import events, protocol
from ..core.clock import now_rfc3339
from ..core.device_clock import ping_payload, update_skew
from ..core.protocol import JsonObject, as_int, list_of, payload_of, text_of
from ..modules.config import store as config_store
from ..modules.devices import service as devices_service
from ..modules.doors import service as doors_service
from ..modules.ota import repository as ota_repository
from ..modules.sessions import service as sessions_service
from .manager import manager

logger = logging.getLogger("ntu-shower.gateway")

router = APIRouter()


@router.websocket("/gateway/device")
async def device_gateway(ws: WebSocket) -> None:
    await ws.accept()
    device_id: str | None = None
    ping_task: asyncio.Task[None] | None = None
    trusted = False
    try:
        hello = protocol.parse(await ws.receive_text())
        if hello is None or hello.get("type") != "link.hello":
            await ws.send_text(protocol.error("proto.hello_required", "first frame must be link.hello"))
            await ws.close()
            return

        hello_payload = payload_of(hello)
        device_id = text_of(hello_payload, "device_id")
        version_code = as_int(hello_payload.get("version_code"))
        row = await run_in_threadpool(devices_service.get_device, device_id)
        trusted = bool(row and row["trusted"])
        # 经 nginx 代理而来：真实设备 IP 在 X-Real-IP 头里
        ip = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
        await run_in_threadpool(
            devices_service.register_device,
            device_id,
            text_of(hello_payload, "app_version"),
            version_code,
            text_of(hello_payload, "os_version"),
            list_of(hello_payload, "capabilities"),
            ip,
        )
        # 安装确认：设备以某 version_code 上线，即证明所有 ≤ 该版本的发布已装在它身上。
        # 必须早于 latest_for，否则本次 hello 仍会推送刚装过的版本——force 发布
        # 会因此形成「下发→下载→安装→重启→再下发」的无限重装循环（真机实测复现）。
        await run_in_threadpool(ota_repository.mark_installed_upto, device_id, version_code)
        manager.register(device_id, ws)
        await ws.send_text(
            protocol.envelope(
                "link.hello_ack",
                {
                    "trusted": trusted,
                    "server_time": now_rfc3339(),
                    "config": await run_in_threadpool(config_store.effective, device_id),
                    "upgrade": await run_in_threadpool(ota_repository.latest_for, device_id, version_code),
                },
                reply_to=text_of(hello, "id"),
            )
        )

        events.publish("device_online", {"device_id": device_id, "trusted": trusted, "at": now_rfc3339()})
        ping_task = asyncio.create_task(_server_ping_loop(ws, device_id))

        while True:
            msg = protocol.parse(await ws.receive_text())
            if msg is None:
                await ws.send_text(protocol.error("proto.bad_json", "frame is not a valid JSON object"))
                continue
            # 未知类型只回错不断开：这是协议的前向扩展点
            await _handle_frame(ws, msg, device_id, trusted)
    except WebSocketDisconnect:
        # 设备离线是常态（移动网络/重启），不作为异常处理
        pass
    finally:
        if ping_task is not None:
            ping_task.cancel()
        if device_id:
            manager.unregister(device_id, ws)
            await run_in_threadpool(devices_service.set_device_offline, device_id)
            events.publish("device_offline", {"device_id": device_id, "at": now_rfc3339()})


async def _handle_frame(ws: WebSocket, msg: JsonObject, device_id: str, trusted: bool) -> None:
    """分发一帧入站报文。未信任设备只放行链路层消息，业务帧静默忽略。"""
    mtype = text_of(msg, "type")
    frame_id = text_of(msg, "id")
    payload = payload_of(msg)

    if mtype == "link.ping":
        await ws.send_text(protocol.envelope("link.pong", {"sender_time": now_rfc3339()}, reply_to=frame_id))
    elif mtype == "link.pong":
        update_skew(device_id, text_of(payload, "sender_time"))
    elif mtype == "config.ack":
        sessions_service.resolve_config_ack(device_id, as_int(payload.get("revision")))
    elif mtype == "door.snapshot":
        if not trusted:
            return
        result = await run_in_threadpool(doors_service.process_snapshot, device_id, payload)
        if result["status"] == "ok":
            await ws.send_text(
                protocol.envelope(
                    "door.snapshot_ack",
                    {"snapshot_id": text_of(payload, "snapshot_id")},
                    reply_to=frame_id,
                )
            )
        else:
            error = result["error"]
            await ws.send_text(protocol.error(str(error["code"]), str(error["message"]), reply_to=frame_id))
    elif mtype == "session.command_result":
        if not trusted:
            return
        if not sessions_service.resolve(text_of(msg, "reply_to"), payload):
            # 回执找不到对应的在途命令（多半是命令已超时收尾）：回错误帧保持
            # 协议完整性反馈，设备端只记日志，不产生副作用。
            await ws.send_text(protocol.error("command.unknown_id", "no pending command for result", reply_to=frame_id))
    else:
        await ws.send_text(protocol.error("proto.unknown_type", f"unknown type: {mtype}"))


async def _server_ping_loop(ws: WebSocket, device_id: str) -> None:
    """按设备有效配置的间隔向设备发服务器 ping，驱动时钟偏差测量。

    连接关闭即正常退出；其他异常记录后退出——静默吞掉会让时钟校准无声失效。
    """
    while True:
        config = await run_in_threadpool(config_store.effective, device_id)
        await asyncio.sleep(as_int(config.get("server_ping_interval_s"), default=60))
        try:
            await ws.send_text(ping_payload())
        except (WebSocketDisconnect, RuntimeError) as exc:
            logger.debug("server ping loop ended for %s: %s", device_id, exc)
            return
