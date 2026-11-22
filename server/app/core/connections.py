"""设备连接注册表：一个 device_id 一条活动连接，重连顶替旧连接。

放在 core 而非网关模块：它被 devices / sessions / ota / bridge / config 等多个
领域作为"设备在不在线"的唯一事实来源读取，而网关路由又依赖那些领域的服务——
留在网关包里会构成 `gateway.routes → devices.service → gateway.*` 的模块环。
它是无依赖的基础设施，下沉到 core 后依赖方向严格向下。
"""

import asyncio

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

SEND_TIMEOUT_S = 5.0


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        #: 每条连接的身份代数：同一设备重连即递增。推送方据此认出"这是新连接"，
        #: 不必等下一次内容变化或保活周期——刚握手的设备应当立刻拿到现态。
        self._generations: dict[str, int] = {}

    def register(self, device_id: str, ws: WebSocket) -> WebSocket | None:
        """注册连接，返回被顶替的旧连接（如有）。

        调用方（网关握手）拿到旧连接后必须关闭它：校园网静默丢包时旧连接在 TCP
        层已死但没有 FIN/RST，旧处理协程挂在 receive_text() 上永不返回——关闭
        旧 WS 让 receive_text() 当场抛 WebSocketDisconnect，finally 才能跑。
        """
        old_ws = self._connections.get(device_id)
        self._connections[device_id] = ws
        self._generations[device_id] = self._generations.get(device_id, 0) + 1
        return old_ws

    def generation_of(self, device_id: str) -> int:
        """这条设备连接的身份代数；没有连接时为 0。

        代数只用于判断"换没换连接"，不是业务状态。调用方直接 monkeypatch
        `_connections` 时（测试替身）拿到的也是 0——同一替身不会被误认成新连接。
        """
        return self._generations.get(device_id, 0)

    def unregister(self, device_id: str, ws: WebSocket) -> bool:
        """注销连接，返回**它是否确实是当前那条**。

        返回值就是"这次断开是否意味着设备离线"的判据：重连是**新连接顶替旧连接**，
        被顶替的那条随后走收尾，此时设备仍在线，返回 False。

        不能用 `is_online` 代替：那回答的是"现在有没有连接"。重连之后旧连接收尾时，
        如果新连接也已断开，`is_online` 同样是假，于是同一次离线被记两遍。
        """
        if self._connections.get(device_id) is ws:
            del self._connections[device_id]
            self._generations.pop(device_id, None)
            return True
        return False

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
            await asyncio.wait_for(ws.send_text(data), timeout=SEND_TIMEOUT_S)
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            # 连接在发送瞬间断开，或对端停止读取：返回 False，让调用方按离线处理。
            return False
        return True

    async def disconnect(self, device_id: str) -> bool:
        """主动断开设备的活动连接（设备会按重连退避自己回来）。

        用于信任位等**只在握手时生效**的状态变更之后：让设备重新 hello，新状态才能
        被它带上。连接不存在返回 False，不抛异常。
        """
        ws = self._connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.close(code=1000)
        except (WebSocketDisconnect, RuntimeError):
            return False
        return True


# 模块级单例。注意：消费方应使用 `from app.core.connections import manager`
# （取得实例），而非 `from app.core import connections`（那会拿到子模块对象）。
manager = ConnectionManager()
