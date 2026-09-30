"""ADB 桥接领域的 API 契约。

`BridgeStatus` 用 `total=False`：桥关闭时只有 `device_id` 与 `open`，打开时才
带端口与两个连接标志。把可选字段显式声明出来，客户端才知道要先判断 `open`。
"""

from typing_extensions import TypedDict


class BridgeOpened(TypedDict):
    """桥打开结果。

    `already_open` 为真时 `generation` 是既有的那次，不是新建的——
    客户端据此区分"刚给你开的"与"之前就开着"。
    """

    device_id: str
    port: int
    already_open: bool
    generation: int


class BridgeClosed(TypedDict):
    """桥关闭结果。"""

    device_id: str
    closed: bool


class BridgeStatus(TypedDict, total=False):
    """桥的当前状态（管理面可观测性）。

    `open` 恒存在；为真时其余字段才有意义。
    `device_connected` 指设备侧连入，`adb_client_connected` 指本机 adb 客户端连入。
    """

    device_id: str
    open: bool
    port: int
    generation: int
    device_connected: bool
    adb_client_connected: bool
