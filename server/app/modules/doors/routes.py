"""喷头现态查询与实时推送。

两个列表端点并存的原因只有一个：小程序已经在用 `/api/v1/doors`，不能动它。
**它们返回完全一样的字段**——权限管的是能不能调这个接口，不是返回什么内容。
"""

import asyncio
import contextlib

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.core import events
from app.core.security import require_ops_token
from app.modules.doors import service

from .schemas import DoorView

router = APIRouter(prefix="/api/v1", tags=["doors"])

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["doors-ops"])


@router.get("/doors")
def list_doors(device_id: str | None = None) -> list[DoorView]:
    return service.list_doors(device_id)


@ops_router.get("/doors")
def list_doors_ops(device_id: str | None = None) -> list[DoorView]:
    """与 `/api/v1/doors` 同源同形，仅多一道 `X-Ops-Token` 校验。"""
    return service.list_doors(device_id)


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
        # 取走它的结果：探测断连的那一帧可能以异常收场，而 asyncio.wait 不消费异常——
        # 不取走会在事件循环里留下 "Task exception was never retrieved"。
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await client_frame
        events.unsubscribe(sid)
        with contextlib.suppress(Exception):
            await ws.close()
