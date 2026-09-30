"""设备 WS 网关平面：/gateway/device。

帧处理规则：
- 第一帧必须是 link.hello，否则报错断开；
- link.ping → link.pong（心跳回路）；
- 未知 type → link.error(proto.unknown_type)，不断开（协议扩展点）；
- 未信任设备保持连接但不进入任何业务。
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from ..core import events, protocol
from ..core.clock import now_rfc3339
from ..core.device_clock import ping_payload, update_skew
from ..modules.config import store as config_store
from ..modules.devices import service as devices_service
from ..modules.doors import service as doors_service
from ..modules.ota import repository as ota_repository
from ..modules.sessions import service as sessions_service
from .manager import manager

router = APIRouter()


@router.websocket("/gateway/device")
async def device_gateway(ws: WebSocket) -> None:
    await ws.accept()
    device_id: str | None = None
    ping_task: asyncio.Task | None = None
    trusted = False
    try:
        raw = await ws.receive_text()
        hello = protocol.parse(raw)
        if hello is None or hello.get("type") != "link.hello":
            await ws.send_text(protocol.error("proto.hello_required", "first frame must be link.hello"))
            await ws.close()
            return

        payload = hello.get("payload", {})
        device_id = str(payload.get("device_id", ""))
        version_code = int(payload.get("version_code") or 0)
        row = await run_in_threadpool(devices_service.get_device, device_id)
        trusted = bool(row and row["trusted"])
        # 经 nginx 代理而来：真实设备 IP 在 X-Real-IP 头里
        ip = ws.headers.get("x-real-ip") or (ws.client.host if ws.client else "")
        await run_in_threadpool(
            devices_service.register_device,
            device_id,
            str(payload.get("app_version", "")),
            version_code,
            str(payload.get("os_version", "")),
            payload.get("capabilities", []),
            ip,
        )
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
                reply_to=str(hello.get("id", "")),
            )
        )

        events.publish("device_online", {"device_id": device_id, "trusted": trusted, "at": now_rfc3339()})
        ping_task = asyncio.create_task(_server_ping_loop(ws, device_id))

        while True:
            raw = await ws.receive_text()
            msg = protocol.parse(raw)
            if msg is None:
                await ws.send_text(protocol.error("proto.bad_json", "frame is not a valid JSON object"))
                continue

            mtype = msg.get("type", "")
            if mtype == "link.ping":
                await ws.send_text(
                    protocol.envelope("link.pong", {"sender_time": now_rfc3339()}, reply_to=str(msg.get("id", "")))
                )
            elif mtype == "link.pong":
                update_skew(device_id, str(msg.get("payload", {}).get("sender_time", "")))
            elif mtype == "config.ack":
                sessions_service.resolve_config_ack(device_id, str(msg.get("payload", {}).get("revision", "")))
            elif mtype == "door.snapshot":
                if trusted:
                    result = await run_in_threadpool(doors_service.process_snapshot, device_id, msg.get("payload", {}))
                    if result["status"] == "ok":
                        await ws.send_text(
                            protocol.envelope(
                                "door.snapshot_ack",
                                {"snapshot_id": str(msg.get("payload", {}).get("snapshot_id", ""))},
                                reply_to=str(msg.get("id", "")),
                            )
                        )
                    else:
                        await ws.send_text(
                            protocol.error(
                                result["error"]["code"],
                                result["error"]["message"],
                                reply_to=str(msg.get("id", "")),
                            )
                        )
            elif mtype == "session.command_result":
                if trusted:
                    resolved = sessions_service.resolve(str(msg.get("reply_to", "")), msg.get("payload", {}))
                    if not resolved:
                        await ws.send_text(
                            protocol.error(
                                "command.unknown_id", "no pending command for result", reply_to=str(msg.get("id", ""))
                            )
                        )
            else:
                await ws.send_text(protocol.error("proto.unknown_type", f"unknown type: {mtype}"))
    except WebSocketDisconnect:
        pass
    finally:
        if ping_task is not None:
            ping_task.cancel()
        if device_id:
            manager.unregister(device_id, ws)
            await run_in_threadpool(devices_service.set_device_offline, device_id)
            events.publish("device_offline", {"device_id": device_id, "at": now_rfc3339()})


async def _server_ping_loop(ws: WebSocket, device_id: str) -> None:
    """按设备有效配置的间隔向设备发服务器 ping，驱动时钟偏差测量。"""
    try:
        while True:
            config = await run_in_threadpool(config_store.effective, device_id)
            await asyncio.sleep(int(config["server_ping_interval_s"]))
            await ws.send_text(ping_payload(device_id))
    except Exception:
        pass
