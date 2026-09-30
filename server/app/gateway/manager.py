"""设备连接注册表：一个 device_id 一条活动连接，重连顶替旧连接。"""

from fastapi import WebSocket


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
        """向指定设备的活动连接发送文本帧；连接不存在或发送异常返回 False。"""
        ws = self._connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_text(data)
            return True
        except Exception:
            return False


# 模块级单例。注意：消费方应使用 `from app.gateway.manager import manager`
# （取得实例），而非 `from app.gateway import manager`（那会拿到子模块对象）。
manager = ConnectionManager()
