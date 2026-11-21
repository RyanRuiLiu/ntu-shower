"""协议消息类型登记表。

**本文件由 tools/sync_contracts.py 生成，不要手改**——消息类型的唯一出处是
`protocol/schema/` 下的 schema 文件名。网关收发与各域下发的信封 `type` 一律
引用这里的常量，禁止裸字符串。
"""

from __future__ import annotations

BRIDGE_CLOSE = "bridge.close"
BRIDGE_OPEN = "bridge.open"
CONFIG_UPDATE = "config.update"
CONFIG_UPDATE_ACK = "config.update_ack"
DEVICE_COMMAND = "device.command"
DEVICE_COMMAND_RESULT = "device.command_result"
DEVICE_LOG_UPLOAD = "device.log_upload"
DEVICE_STATUS = "device.status"
DOOR_SNAPSHOT = "door.snapshot"
DOOR_SNAPSHOT_ACK = "door.snapshot_ack"
LINK_ERROR = "link.error"
LINK_HELLO = "link.hello"
LINK_HELLO_ACK = "link.hello_ack"
LINK_PING = "link.ping"
LINK_PONG = "link.pong"
OTA_PROGRESS = "ota.progress"
OTA_UPDATE_AVAILABLE = "ota.update_available"
STATUS_LINE_UPDATE = "status_line.update"

ALL: frozenset[str] = frozenset(
    [
        "bridge.close",
        "bridge.open",
        "config.update",
        "config.update_ack",
        "device.command",
        "device.command_result",
        "device.log_upload",
        "device.status",
        "door.snapshot",
        "door.snapshot_ack",
        "link.error",
        "link.hello",
        "link.hello_ack",
        "link.ping",
        "link.pong",
        "ota.progress",
        "ota.update_available",
        "status_line.update",
    ]
)
