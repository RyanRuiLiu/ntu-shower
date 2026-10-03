"""喷头域 API：现态查询（GET /doors）与实时推送（WS /stream）。

`GET /doors` 是喷头现态的初始装载与重连恢复入口；持续更新由 /stream 的
door.update 数据承载推送完成（载荷与 GET 同一 service 路径产出，客户端直接
应用，见 events 的载荷约定）。

`WS /stream` 向订阅者转发事件总线；带 `device_id` 时服务端只推该设备的
事件（握手期过滤，无订阅协议）。
"""

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import events
from app.modules.doors import service

from .schemas import DoorView

router = APIRouter(prefix="/api/v1", tags=["doors"])


@router.get("/doors")
def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表；带 device_id 即该设备的喷头，不带列全部设备。"""
    return service.list_doors(device_id)


@router.websocket("/stream")
async def stream(ws: WebSocket, device_id: str | None = None) -> None:
    """实时推送：door.update（喷头变化）与 device.update（设备在场变化）。

    带 `device_id` 时只推该设备的事件：过滤在服务端做，客户端不复制规则。

    该连接是**单向的**：客户端不得发送任何帧——服务端把"收到客户端帧"当作主动
    断开的信号（见循环体），链路保活由传输层 ping/pong 承担，应用层无事可做。

    断线恢复的职责在客户端：重连后重拉一次 `GET /doors`（断线窗口内错过的事件
    不含状态），此后推送即权威。"""
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
            message = pending.result()
            if device_id is not None and not _mentions_device(message, device_id):
                continue
            await ws.send_text(message)
    except (WebSocketDisconnect, RuntimeError):
        # 客户端在推送途中消失：属正常结束，收尾走 finally
        pass
    finally:
        client_frame.cancel()
        # 取走它的结果：探测断连的那一帧可能以异常收场，而 asyncio.wait 不消费异常
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await client_frame
        events.unsubscribe(sid)
        with contextlib.suppress(Exception):
            await ws.close()


def _mentions_device(message: str, device_id: str) -> bool:
    """判断一条事件是否属于目标设备；解析失败的事件按不属于处理（不外泄）。"""
    try:
        payload = json.loads(message).get("payload")
        return isinstance(payload, dict) and payload.get("device_id") == device_id
    except ValueError:
        return False
