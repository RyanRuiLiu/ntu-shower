"""用户 API（v1）：喷头现态查询与实时推送（只读，匿名可访问）。"""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import events
from app.core.protocol import JsonObject
from app.modules.doors import repository

router = APIRouter(prefix="/api/v1", tags=["doors"])


@router.get("/doors")
def list_doors(device_id: str | None = None) -> list[JsonObject]:
    return repository.list_doors_with_device(device_id)


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """实时推送：door_update / device 事件（进程内事件总线转发）。"""
    await ws.accept()
    sid, queue = events.subscribe()
    client_frame = asyncio.create_task(ws.receive_text())  # 仅用于探测断连
    try:
        while True:
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({pending, client_frame}, return_when=asyncio.FIRST_COMPLETED)
            if client_frame in done:
                # 客户端主动发来任何帧（含关闭）→ 结束
                pending.cancel()
                break
            await ws.send_text(pending.result())
    except (WebSocketDisconnect, RuntimeError):
        # 客户端在推送途中消失：属正常结束，收尾走 finally
        pass
    finally:
        client_frame.cancel()
        events.unsubscribe(sid)
        with contextlib.suppress(Exception):
            await ws.close()
