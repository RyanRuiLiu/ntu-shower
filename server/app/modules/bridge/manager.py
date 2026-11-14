"""ADB 桥：唯一的建立点与回收点。

一条桥持有本机回环监听套接字、到设备的 WS、本机 TCP 流三样资源，生命周期
完全一致，因此建立与销毁各只有一处：AdbBridge.open 建立（成功后才进注册表），
AdbBridge.recycle 销毁并摘除注册表。显式关桥、设备 WS 断开、寿命到点等全部
结束路径都只调 recycle，不做任何自有清理；分散收尾会漏掉监听套接字，端口
被占后每次开桥都是 Errno 98。

监听端口按设备固定分配，运维的 adb connect 脚本可以写死；重开时上一轮监听
尚未完全释放，open 为此退避重试。
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from app.core.config import settings
from app.core.logging import log_event
from app.core.problems import PORT_UNAVAILABLE, ApiProblemError

BRIDGE_BUFFER_SIZE = 65536
PENDING_MAX = 1024 * 1024  # 对端未就绪时的有界缓冲

#: 监听套接字接到一个 adb 客户端时的处理函数。写成字符串是因为注解在类定义时
#: 求值，而这个别名必须排在类之前才能被方法签名引用。
ClientHandler = Callable[["AdbBridge", asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]

#: 监听端口被上一轮占用时的退避重试上限，合计约 4.5 秒，压在本项目的单例超时
#: 约定（5 秒）以内；再长就该去查端口为什么没放。
BIND_ATTEMPTS = 5
BIND_BACKOFF_S = 0.3


class AdbBridge:
    """一条 ADB 桥。只有 open 能创建它，只有 recycle 能销毁它。

    不做成 dataclass：字段之间有一致性要求（有 tcp_writer 才有 tcp_reader、server
    只在建立成功后非空），构造过程由 open 收在一处，不会出现半成品。
    """

    def __init__(self, device_id: str, port: int, generation: int) -> None:
        self.device_id = device_id
        self.port = port
        self.generation = generation
        self.opened_at = 0.0
        self.server: asyncio.AbstractServer | None = None
        self.device_ws: WebSocket | None = None
        self.tcp_writer: asyncio.StreamWriter | None = None
        self.tcp_reader: asyncio.StreamReader | None = None
        self.pump_task: asyncio.Task[None] | None = None
        self.pending_to_device = bytearray()
        self.pending_to_tcp = bytearray()
        self.recycled = False

    @classmethod
    async def open(cls, device_id: str, generation: int, port: int, on_client: ClientHandler) -> "AdbBridge":
        """建桥：绑定本机回环监听，成功后登记进注册表。

        失败一律抛出且不留半个桥：没进注册表的东西没人回收，因此这里保证要么
        完整（监听已绑、已在注册表），要么什么都不存在。登记也在此完成，调用方
        不必记得 attach。
        """
        bridge = cls(device_id, port, generation)
        bridge.opened_at = asyncio.get_running_loop().time()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await on_client(bridge, reader, writer)

        server = None
        last_error: OSError | None = None
        for attempt in range(BIND_ATTEMPTS):
            try:
                server = await asyncio.start_server(handle, "127.0.0.1", port)
                break
            except OSError as exc:
                last_error = exc
                await asyncio.sleep(BIND_BACKOFF_S * (attempt + 1))
        if server is None:
            raise ApiProblemError(PORT_UNAVAILABLE, f"端口 {port} 被占用：{last_error}")

        bridge.server = server
        registry.attach(bridge)
        return bridge

    @property
    def connected(self) -> bool:
        """设备 WS（隧道半边）就绪。"""
        return self.device_ws is not None

    @property
    def has_adb_client(self) -> bool:
        """adb 客户端（TCP 半边）已接入。"""
        return self.tcp_writer is not None

    def bind_device(self, ws: WebSocket) -> None:
        """把已通过配对校验的设备 WS 接到这条桥上（nonce 消费在路由侧完成）。"""
        self.device_ws = ws

    def attach_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """接入一个 adb 客户端；已有客户端时拒绝。

        一个世代只服务一个客户端：并发的 adb 各自发 service 请求，隧道只有一条，
        交错后无法对应请求与响应。
        """
        if self.tcp_writer is not None:
            return False
        self.tcp_reader = reader
        self.tcp_writer = writer
        return True

    async def detach_client(self) -> None:
        """断开当前 adb 客户端，保留桥，回到等待下一个客户端的状态。

        泵任务取消后不 await：TCP socket 关闭会让它的 read 立即抛异常退出，等待
        反而会在 WS 断开与 drain 并发时形成相互等待。
        """
        if self.pump_task is not None and not self.pump_task.done():
            self.pump_task.cancel()
        self.pump_task = None
        if self.tcp_writer is not None:
            # 对端可能已消失，关闭动作会抛；清理路径必须继续走完
            with contextlib.suppress(Exception):
                self.tcp_writer.close()
        self.tcp_writer = None
        self.tcp_reader = None
        self.pending_to_tcp.clear()

    async def recycle(self, reason: str) -> None:
        """拆掉这条桥并从注册表摘除，幂等。

        reason 只用于日志，但必需：多条结束路径可能同时关同一条桥，而日志必须
        能回答这条桥何时、因何被关。幂等同样必需：asyncio.Server.close() 重复
        调用会撞上内部已清空的成员。
        """
        if self.recycled:
            return
        self.recycled = True
        log_event(
            "INFO",
            "bridge.recycled",
            "ADB 桥已回收",
            device_id=self.device_id,
            port=self.port,
            generation=self.generation,
            reason=reason,
        )

        await self.detach_client()
        if self.device_ws is not None:
            # 连接可能已经断了，关闭失败不影响回收
            with contextlib.suppress(Exception):
                await self.device_ws.close()
            self.device_ws = None
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
            self.server = None
        self.pending_to_device.clear()
        registry.detach(self)


class BridgeRegistry:
    """全局索引：只回答"这台设备现在有没有桥"，不掌管桥的生命周期。

    四份映射是四种不同的信息（待配对、已建、端口分配、世代），改写只发生在
    attach 与 detach 两处。
    """

    def __init__(self) -> None:
        # device_id → (generation, nonce, 下发时刻)。带时刻让"设备一直不连"的
        # 待配对状态能过期，而不是永久挂起。
        self.pending: dict[str, tuple[int, str, float]] = {}
        self._active: dict[str, AdbBridge] = {}
        self._ports: dict[str, int] = {}
        self._generation: dict[str, int] = {}

        # 开与关共用一把全局锁：两者中间都有 await，会让出事件循环，并发请求会
        # 交错读到半成品状态。开关桥是罕见操作，串行化代价可忽略。
        self.op_lock = asyncio.Lock()

    def get(self, device_id: str) -> AdbBridge | None:
        return self._active.get(device_id)

    def attach(self, bridge: AdbBridge) -> None:
        self._active[bridge.device_id] = bridge

    def detach(self, bridge: AdbBridge) -> None:
        """只摘自己的那一条。

        按对象比对而非按 device_id：同一台设备可能已换了新桥（旧桥的回收晚到），
        按 id 摘会摘掉新桥，它的监听从此无人回收。
        """
        if self._active.get(bridge.device_id) is bridge:
            del self._active[bridge.device_id]

    def open_session(self, device_id: str, now: float) -> tuple[int, int, str]:
        """开一个新会话世代：世代严格递增、端口稳定、nonce 一次性。"""
        port = self._ports.setdefault(device_id, settings.bridge_base_port + len(self._ports))
        generation = self._generation.get(device_id, 0) + 1
        self._generation[device_id] = generation
        nonce = uuid.uuid4().hex
        self.pending[device_id] = (generation, nonce, now)
        return generation, port, nonce

    def consume_nonce(self, device_id: str, nonce: str, now: float, timeout_s: float) -> int | None:
        """校验并消费 nonce，返回其世代；无效或已过期返回 None。"""
        entry = self.pending.get(device_id)
        if entry is None or entry[1] != nonce:
            return None
        del self.pending[device_id]
        if now - entry[2] > timeout_s:
            return None
        return entry[0]

    def reset(self) -> None:
        """清空全部会话状态（测试隔离用）。

        监听套接字一并关闭：端口按设备固定分配，只清字典会让下一个用例开桥时
        撞上残留监听。整段套 suppress 且不 await：这常在事件循环已关闭之后被调
        （pytest 反向销毁夹具），关闭已无效果，但不应让 teardown 报错。
        """
        with contextlib.suppress(Exception):
            for bridge in self._active.values():
                bridge.recycled = True
                if bridge.server is not None and bridge.server.is_serving():
                    bridge.server.close()
                bridge.server = None
        # 重建锁：用例中途失败可能留下一个被持有的锁，下一个用例会永久等它
        self.op_lock = asyncio.Lock()
        self.pending.clear()
        self._active.clear()
        self._ports.clear()
        self._generation.clear()


registry = BridgeRegistry()
