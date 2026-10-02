"""设备连接注册表：一个 device_id 一条活动连接，重连顶替旧连接。

放在 core 而非 gateway：它被 devices / sessions / ota / bridge / config 等多个
领域作为"设备在不在线"的唯一事实来源读取，而 gateway.routes 又依赖那些领域的
服务——留在 gateway 包里会构成 modules ↔ gateway 的包级循环。它是无依赖的
基础设施，下沉到 core 后依赖方向严格向下。
"""

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    def register(self, device_id: str, ws: WebSocket) -> bool:
        replaced = device_id in self._connections
        self._connections[device_id] = ws
        return replaced

    def unregister(self, device_id: str, ws: WebSocket) -> None:
        if self._connections.get(device_id) is ws:
            del self._connections[device_id]

    def get(self, device_id: str) -> WebSocket | None:
        return self._connections.get(device_id)

    def is_online(self, device_id: str) -> bool:
        return device_id in self._connections

    def online_connections(self) -> list[tuple[str, WebSocket]]:
        return list(self._connections.items())

    async def send_to(self, device_id: str, data: str) -> bool:
        """向指定设备的活动连接发送文本帧；连接不存在或已断开返回 False。

        调用方据返回值决策（例如把命令标为 device.offline），因此这里不抛异常。
        """
        ws = self._connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_text(data)
        except (WebSocketDisconnect, RuntimeError):
            # 连接在发送瞬间断开：Starlette 抛 RuntimeError 的语义即"连接不可用"
            return False
        return True


# 模块级单例。注意：消费方应使用 `from app.core.connections import manager`
# （取得实例），而非 `from app.core import connections`（那会拿到子模块对象）。
manager = ConnectionManager()
