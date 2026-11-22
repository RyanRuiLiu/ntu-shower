"""部署入口：持有 uvicorn Server 子类——关闭序列先向设备发 1001，再交还 uvicorn。

uvicorn 收到关闭信号后的 shutdown 先对全部连接发 1012（websockets 实现硬编码），
之后才进入 lifespan——应用在 lifespan 里的任何发码都晚于它（2026-11-22 生产实证：
设备在部署重启时记录 "Code 1012 is reserved"，OkHttp 以协议错误拒收基础设施保留码）。
本类把 1001（Going Away）提到 shutdown 最前：连接先被正常关闭，uvicorn 对已关
连接的收尾不再发码，设备看到的是部署断开而非协议错误。
"""

import socket
from contextlib import suppress

import uvicorn

from app.core.connections import manager
from app.core.protocol import WS_CLOSE_GOING_AWAY


async def _close_device_connections() -> None:
    """向全部在线设备发 1001 关闭帧。

    不在这里做收尾（记 link event、刷 last_seen）——那些由各连接自己的 finally
    路径完成，这里只负责让设备收到一个协议上正确的关闭码。
    """
    for _device_id, ws in manager.online_connections():
        with suppress(Exception):
            await ws.close(code=WS_CLOSE_GOING_AWAY)


class Server(uvicorn.Server):
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        await _close_device_connections()
        await super().shutdown(sockets)


if __name__ == "__main__":
    Server(uvicorn.Config("app.main:app", host="0.0.0.0", port=8110)).run()
