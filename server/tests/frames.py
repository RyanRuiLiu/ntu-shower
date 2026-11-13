"""协议帧构造与读取：link.hello、任意帧、按类型读取。

服务端在 link.hello 之后会主动探测 ping，测试读帧时必须对额外帧免疫。
"""

import json
import uuid
from typing import Any

from starlette.testclient import WebSocketTestSession


def hello_frame(
    device_id: str,
    *,
    version_code: int = 6,
    capabilities: list[str] | None = None,
    last_offline_reason: str | None = None,
    last_offline_at: str | None = None,
    v: int = 1,
) -> str:
    """构造 link.hello 帧；capabilities 为 None 时空声明。

    last_offline_* 是 0.4.35 起的断开原因回报；不给就与老设备一致（帧里不带）。
    """
    payload = {
        "device_id": device_id,
        "app_version": "0.4.13",
        "version_code": version_code,
        "os_version": "4.4.4",
        "capabilities": capabilities if capabilities is not None else [],
    }
    if last_offline_reason is not None:
        payload["last_offline_reason"] = last_offline_reason
    if last_offline_at is not None:
        payload["last_offline_at"] = last_offline_at
    return json.dumps(
        {
            "v": v,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": payload,
        }
    )


def frame(msg_type: str, payload: dict[str, Any], *, reply_to: str | None = None, v: int = 1) -> str:
    """构造任意协议帧。"""
    message: dict[str, Any] = {
        "v": v,
        "type": msg_type,
        "id": str(uuid.uuid4()),
        "ts": "2026-09-30T12:00:00.000Z",
        "payload": payload,
    }
    if reply_to is not None:
        message["reply_to"] = reply_to
    return json.dumps(message)


def recv_until(ws: WebSocketTestSession, msg_type: str) -> dict[str, Any]:
    """读到指定类型的帧再返回，中间帧丢弃。

    服务端在 link.hello 之后会立刻主动探测一轮 ping（尽快建立时钟偏差），测试对
    这类额外帧必须免疫。
    """
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg
