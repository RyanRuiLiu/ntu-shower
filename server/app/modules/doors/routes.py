"""用户 API（v1）：喷头现态查询与实时推送（只读，匿名可访问）。"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import events
from app.modules.doors import repository

router = APIRouter(prefix="/api/v1", tags=["doors"])


@router.get("/doors")
def list_doors(device_id: str | None = None) -> list[dict]:
    return repository.list_doors_with_device(device_id)


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """实时推送：door_update / device 事件（进程内事件总线转发）。"""
    await ws.accept()
    sid, queue = events.subscribe()
    got_client = asyncio.ensure_future(ws.receive_text())  # 仅用于探测断连
    try:
        while True:
            get_msg = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {get_msg, got_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if got_client in done:
                # 客户端主动发来任何帧（含关闭）→ 结束
                get_msg.cancel()
                break
            msg = get_msg.result()
            await ws.send_text(msg)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        got_client.cancel()
        events.unsubscribe(sid)
        try:
            await ws.close()
        except Exception:
            pass
