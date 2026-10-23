"""测试共享工具：协议帧构造、设备档案登记、可信在线连接、网关连接替身、APK 合成。"""

import io
import json
import struct
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


def hello_frame(
    device_id: str,
    *,
    version_code: int = 6,
    capabilities: list[str] | None = None,
    last_offline_reason: str | None = None,
    last_offline_at: str | None = None,
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
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": payload,
        }
    )


def frame(msg_type: str, payload: dict[str, Any], *, reply_to: str | None = None) -> str:
    """构造任意协议帧。"""
    message: dict[str, Any] = {
        "v": 1,
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


def register_trusted_device(device_id: str, capabilities: list[str] | None = None) -> None:
    """在设备档案里登记并信任一台设备（默认无能力声明，即未声明态）。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(
        device_id, "0.4.13", 57, "4.4.4", capabilities or [], "127.0.0.1", "2026-06-01T08:00:00.000Z"
    )
    devices_repository.set_trusted(device_id, True)


@contextmanager
def trusted_online(
    client: TestClient,
    device_id: str,
    admin_headers: dict[str, str],
    *,
    version_code: int = 6,
    capabilities: list[str] | None = None,
) -> Iterator[WebSocketTestSession]:
    """建档、信任、重连，产出一个保持在线的设备 WS 会话。

    上下文管理器保证断言失败时连接也会关闭：泄漏的连接会让 TestClient 的 portal
    挂起，表现为整个测试会话卡死。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(hello_frame(device_id, version_code=version_code, capabilities=capabilities))
        recv_until(ws, "link.hello_ack")
    client.patch(f"/api/v1/devices/{device_id}", headers=admin_headers, json={"trusted": True})
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(hello_frame(device_id, version_code=version_code, capabilities=capabilities))
        recv_until(ws, "link.hello_ack")
        yield ws


class FakeGatewayWS:
    """替代设备控制连接：捕获管理面下发的帧，供断言读取。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> bool:
        self.sent.append(data)
        return True

    def last_frame(self) -> dict[str, Any]:
        return dict(json.loads(self.sent[-1]))


class DeadGatewayWS:
    """发送即失败的设备连接：验证下发路径对发送失败的处置。"""

    async def send_text(self, data: str) -> bool:
        message = "connection gone"
        raise RuntimeError(message)


def build_apk(version: str, version_code: int, *, utf8: bool = False) -> bytes:
    """合成一个只含 AndroidManifest.xml 的 ZIP，版本属性按 aapt 的真实块布局生成。

    布局逐段对齐真机产物：字符串池 → 资源映射表 → start-element。资源映射表按 aapt
    约定与字符串池**前缀**按下标对齐——属性名字符串必须排在池的最前面。真机 APK 太大
    不进仓库，发布校验与 AXML 解析器的测试都用它。
    """
    attr_names = ("versionCode", "versionName")
    strings = [*attr_names, "manifest", str(version)]

    pool_body = bytearray()
    offsets: list[int] = []
    for text in strings:
        offsets.append(len(pool_body))
        if utf8:
            raw = text.encode("utf-8")
            pool_body += bytes([len(raw), len(raw)]) + raw + b"\x00"
        else:
            pool_body += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\x00\x00"
    flags = (1 << 8) if utf8 else 0
    pool = (
        struct.pack(
            "<HHIIIIII",
            0x0001,
            28,
            28 + 4 * len(strings) + len(pool_body),
            len(strings),
            0,
            flags,
            28 + 4 * len(strings),
            0,
        )
        + struct.pack(f"<{len(strings)}I", *offsets)
        + bytes(pool_body)
    )

    resmap = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(attr_names)) + struct.pack(
        f"<{len(attr_names)}I", 0x0101021B, 0x0101021C
    )

    def attr(name_index: int, data_type: int, data: int, raw: int) -> bytes:
        # TypedValue：size u16（=8）+ res0 u8（=0）+ dataType u8 + data u32，合成一个 u32 放前半
        return struct.pack("<IIII", 0xFFFFFFFF, name_index, raw, 8 | (data_type << 24)) + struct.pack("<I", data)

    attributes = attr(0, 0x10, version_code, 0xFFFFFFFF) + attr(
        1, 0x03, strings.index(str(version)), strings.index(str(version))
    )
    start_element = (
        struct.pack("<HHI", 0x0102, 16, 16 + 20 + len(attributes))
        + struct.pack("<II", 1, 0xFFFFFFFF)
        + struct.pack("<IIHHHHHH", 0xFFFFFFFF, strings.index("manifest"), 20, 20, 2, 0, 0, 0)
        + attributes
    )

    total = len(pool) + len(resmap) + len(start_element)
    xml = struct.pack("<HHI", 0x0003, 8, 8 + total) + pool + resmap + start_element

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", xml)
    return buffer.getvalue()
