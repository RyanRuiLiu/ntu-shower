"""协议信封单一来源（docs/PROTOCOL.md）。

所有 WebSocket 帧的组装与解析只在此模块进行，与 protocol/schema 保持一一对应：
- v：协议版本（当前 1）；
- type：消息类型（命名空间.动作，如 link.hello / door.snapshot）；
- id：发送方生成的唯一帧 ID，应答帧以 reply_to 携带；
- ts：发送时刻（RFC3339）；
- payload：类型对应的业务负载。
"""

import json
import uuid
from typing import Any

from app.core.clock import now_rfc3339

PROTOCOL_VERSION = 1

# 协议负载的形状：设备报文是外部 JSON 输入，逐字段建模会与 protocol/schema 重复。
# 用别名而不是裸 Any——既表达"这是 JSON 边界"的意图，也满足签名不得出现 Any 的规则。
JsonObject = dict[str, Any]


def envelope(msg_type: str, payload: dict[str, Any], reply_to: str | None = None, frame_id: str | None = None) -> str:
    """组装出站信封（JSON 文本帧）。

    frame_id：调用方指定的帧 ID（如 command_id，设备按它回 reply_to）；
    缺省时自动生成 UUID。
    """
    frame: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "id": frame_id if frame_id is not None else str(uuid.uuid4()),
        "ts": now_rfc3339(),
        "payload": payload,
    }
    if reply_to:
        frame["reply_to"] = reply_to
    return json.dumps(frame, separators=(",", ":"))


def error(code: str, message: str, reply_to: str | None = None) -> str:
    """组装 link.error 帧（code 为结构化错误码，见 protocol/schema/errors）。"""
    return envelope("link.error", {"code": code, "message": message}, reply_to)


def parse(raw: str) -> JsonObject | None:
    """解析入站帧；非 JSON 对象返回 None（调用方回 proto.bad_json）。"""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def payload_of(frame: JsonObject) -> JsonObject:
    """取信封的 payload；形状非法时返回空对象。

    设备报文是外部输入，字段形状不可假定——取值一律经由本模块的辅助函数，
    避免在各处重复写 isinstance 判断。
    """
    payload = frame.get("payload")
    return payload if isinstance(payload, dict) else {}


def text_of(frame: JsonObject, key: str) -> str:
    """取字符串字段；缺失或类型不符时返回空串。"""
    value = frame.get(key)
    return value if isinstance(value, str) else ""


def list_of(frame: JsonObject, key: str) -> list[Any]:
    """取列表字段；缺失或类型不符时返回空列表。"""
    value = frame.get(key)
    return value if isinstance(value, list) else []


def as_int(value: object, default: int = 0) -> int:
    """把外部 JSON 标量安全转为 int；类型不符或不可转换时取默认值。"""
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    return default
