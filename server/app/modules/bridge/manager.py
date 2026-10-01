"""ADB 桥：**单一建立点、唯一回收点**。

一条桥持有三样资源——本机回环监听套接字、到设备的 WS、本机 TCP 流。三者的生命周期
完全一致，所以建立与销毁各只能有一处：

    `AdbBridge.open()`     建立，成功后才进注册表
    `AdbBridge.recycle()`  销毁，并从注册表摘除

四条结束路径——显式关桥、设备 WS 断开、空闲超时、寿命到点——**都只调 `recycle()`**，
自己不做任何清理。此前它们各自收尾，于是"关监听、摘注册表、清设备 WS"被写成了三份，
每份都不完整：漏掉监听套接字那次，端口被永久占着，之后每次开桥都是 `Errno 98`，
而设备侧明明已经连上来（日志里有 `/gateway/bridge` 的 accepted）。

## 为什么端口按设备固定分配

运维的 `adb connect` 脚本因此可以写死，不必每次去查端口。代价是重开时必然撞上同一个
端口，而上一轮监听关闭后内核要一点时间才让它可复用——`open()` 为此退避重试。
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from app.core.config import settings
from app.core.problems import PORT_UNAVAILABLE, ApiProblemError

BRIDGE_BUFFER_SIZE = 65536
PENDING_MAX = 1024 * 1024  # 对端未就绪时的有界缓冲

#: 监听套接字接到一个 adb 客户端时的处理函数。桥把它绑给 `start_server`，而它拿得到桥
#: 本身——处理一个客户端必然要读写桥的缓冲与设备 WS。`AdbBridge` 写成字符串是因为注解
#: 在类定义时就会求值，而这个别名必须排在类之前才能被它的方法签名引用。
ClientHandler = Callable[["AdbBridge", asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]

#: 监听端口被上一轮占用时的退避重试上限（次）。合计约 4.5 秒——压在 5 秒以内是刻意的，
#: 那是本项目的单例超时约定；再长就该去查端口为什么没放，而不是继续等。
BIND_ATTEMPTS = 5
BIND_BACKOFF_S = 0.3


class AdbBridge:
    """一条 ADB 桥。**只有 [open] 能创建它，只有 [recycle] 能销毁它。**

    不做成 dataclass：它的字段之间有一致性要求（有 tcp_writer 才有 tcp_reader、
    server 只在建立成功后才非空），构造过程被 [open] 收在一处才不会出现半成品。
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
        """建桥：绑本机回环监听，成功后才登记进注册表。

        失败一律抛，且**不留半个桥**——没进注册表的东西没人会去回收它，所以这里由自己
        保证：要么完整（监听已绑、已在注册表里），要么什么都不存在。登记也在这里做，
        调用方不必记得去 attach——少一个能忘的步骤就少一处不一致。
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

    def accept_device(self, ws: WebSocket, nonce: str, now: float, pair_timeout_s: float) -> bool:
        """把一个设备 WS 接到这条桥上；不属于本桥的返回 False。

        判据只有 nonce 一条，因为它是**一次性**的：`open_session` 每发一个新 nonce 就把它
        记下，`consume_nonce` 用过即删。所以旧世代重放拿的是已删的 nonce，自然通不过——
        不需要再比一次世代号。此前路由层同时做 nonce 校验、世代比较、以及"桥不在册就
        现建一条"三件事，于是建桥多出一个入口，那个入口建出来的桥没有监听套接字，
        谁也回收不了它。
        """
        if not registry.consume_nonce(self.device_id, nonce, now, pair_timeout_s):
            return False
        self.device_ws = ws
        return True

    def attach_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        """接入一个 adb 客户端；已有客户端时拒绝（返回 False）。

        一个世代只服务一个客户端：并发的 adb 会各自发 service 请求，而隧道只有一条，
        交错之后谁也分不清哪个响应对应哪个请求。
        """
        if self.tcp_writer is not None:
            return False
        self.tcp_reader = reader
        self.tcp_writer = writer
        return True

    async def detach_client(self) -> None:
        """断开当前 adb 客户端，**保留桥**——回到等下一个客户端（旧系统的隧道语义）。

        泵任务取消后不 await：TCP socket 关闭会让它的 read 立即抛异常退出，等待反而会
        在 WS 断开与 drain 并发时形成相互等待（真实死锁风险）。
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

    async def recycle(self) -> None:
        """拆掉这条桥，并把自己从注册表摘除。**幂等**。

        幂等不是可选的：四条结束路径可能同时想关同一条桥，谁先谁后不确定，而
        `asyncio.Server.close()` 重复调用会撞上内部已清空的成员（`NoneType._stop_serving`）。
        """
        if self.recycled:
            return
        self.recycled = True

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
    """全局索引：**只回答"这台设备现在有没有桥"**，不掌管桥的生命周期。

    这里保留四份映射，因为它们是四种不同的信息（正在配对的、已建的、端口分配、世代）。
    关键是**只有一处**改它们——[attach] 与 [detach]——而不是像此前那样由路由、WS 处理
    函数、回收函数各自去动，那样每次改动都要让四者保持一致，补丁只会越打越多。
    """

    def __init__(self) -> None:
        #: device_id → (generation, nonce, 下发时刻)。带时刻是为了让"设备一直不连"
        #: 的待配对状态能过期，而不是永久挂在那里。
        self.pending: dict[str, tuple[int, str, float]] = {}
        self._active: dict[str, AdbBridge] = {}
        self._ports: dict[str, int] = {}
        self._generation: dict[str, int] = {}

        #: 开与关的互斥锁。两者中间都有 await（start_server / wait_closed）会让出事件
        #: 循环，并发到达的请求会交错读到半成品状态。一把全局锁而不是每设备一把：
        #: 开关桥是罕见操作，串行化的代价可忽略，而少一层按设备管理的锁字典就少一处
        #: 泄漏点。
        self.op_lock = asyncio.Lock()

    def get(self, device_id: str) -> AdbBridge | None:
        return self._active.get(device_id)

    def attach(self, bridge: AdbBridge) -> None:
        self._active[bridge.device_id] = bridge

    def detach(self, bridge: AdbBridge) -> None:
        """只摘自己的那一条。

        按对象比对而不是按 device_id：同一台设备可能已经换了新桥（旧桥的回收晚到），
        那时按 id 摘会摘掉新的那条，它的监听从此无人回收——实测过一次，
        此后每次开桥都是端口占用。
        """
        if self._active.get(bridge.device_id) is bridge:
            del self._active[bridge.device_id]

    def open_session(self, device_id: str, now: float) -> tuple[int, int, str]:
        """开一个新会话世代：世代严格递增（同设备跨会话可比较）、端口稳定、nonce 一次性。"""
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

        监听套接字也关：只清字典的话，用例之间会残留监听端口，而端口是**固定分配**的
        （同一台设备每次拿同一个），下一个用例开桥必然 `Errno 98`。

        整段套 `suppress` 且不 await：这是最后一道回收，常在事件循环已经关掉之后被调
        （pytest 反向销毁夹具，`client` 先退出）。那时关闭既无效果也无必要，但不该让
        teardown 报错——它会把一个通过的用例记成 ERROR。`server.close()` 本身是同步的
        （只是停止 accept），所以在这里调不违反"同步重置"的前提。
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
