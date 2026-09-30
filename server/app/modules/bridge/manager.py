"""ADB 桥接注册表：世代会话、按需建连、幂等生命周期。

设计依据（真实标准，非自造）：
- OWASP WebSocket Cheat Sheet：WSS + 每会话令牌（ADB 本身无认证，必须在桥层做）；
- STF/DeviceFarmer 模式：控制连接多路复用，ADB 会话按需建立、用毕回收——
  服务器同时承载其他服务，空闲不占连接；
- nginx WS 代理 read timeout 默认 60s：设备桥 WS 走 3600s 配置（deploy/nginx），
  另以空闲超时（BRIDGE_IDLE_TIMEOUT_S）在应用层回收真正空闲的 adb 会话；
- 世代令牌：每次 open 推进 generation，旧会话的异步回调按世代丢弃
  （杜绝旧项目 "stale state 强制重启" 补丁）。
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

from app.core.config import settings

BRIDGE_BUFFER_SIZE = 65536
PENDING_MAX = 1024 * 1024  # 对端未就绪时的有界缓冲


@dataclass
class AdbBridge:
    """单台设备的桥接会话（世代隔离）。"""

    device_id: str
    port: int
    generation: int = 0
    #: 打开时刻（事件循环时间）。**绝对寿命**据此计算——空闲超时管不到"一直有流量"
    #: 的情况，而调试通道不该无限期开着。
    opened_at: float = 0.0
    device_ws: WebSocket | None = None
    tcp_writer: asyncio.StreamWriter | None = None
    tcp_reader: asyncio.StreamReader | None = None
    server: asyncio.AbstractServer | None = None
    pump_task: asyncio.Task[None] | None = None
    pending_to_device: bytearray = field(default_factory=bytearray)
    pending_to_tcp: bytearray = field(default_factory=bytearray)

    @property
    def connected(self) -> bool:
        """设备 WS（隧道半边）就绪。"""
        return self.device_ws is not None

    @property
    def has_adb_client(self) -> bool:
        """adb 客户端（TCP 半边）已接入。"""
        return self.tcp_writer is not None

    async def close_stream(self) -> None:
        """仅断 TCP 流与泵（保留监听，回到等待下一个 adb 连接——旧项目语义）。幂等。

        泵任务取消后不 await：TCP socket 关闭会让泵的 read 立即抛异常退出，
        等待反而会在 WS 断开与 drain 并发时形成相互等待（真实死锁风险）。
        """
        if self.pump_task and not self.pump_task.done():
            self.pump_task.cancel()
        self.pump_task = None
        if self.tcp_writer is not None:
            # 对端可能已消失，关闭动作会抛；清理路径必须继续走完
            with contextlib.suppress(Exception):
                self.tcp_writer.close()
        self.tcp_writer = None
        self.tcp_reader = None
        self.pending_to_tcp.clear()

    async def close(self) -> None:
        """整体回收：流 + 监听 + 设备 WS。幂等。"""
        await self.close_stream()
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
            self.server = None
        if self.device_ws is not None:
            # 连接可能已经断了，关闭动作失败不影响回收流程
            with contextlib.suppress(Exception):
                await self.device_ws.close()
            self.device_ws = None
        self.pending_to_device.clear()


class BridgeRegistry:
    """全局唯一注册表。"""

    def __init__(self) -> None:
        # device_id → (generation, nonce, 下发时刻)；带时刻是为了让"设备一直不连"
        # 的待配对状态能过期，而不是永久挂在那里
        self.pending: dict[str, tuple[int, str, float]] = {}
        self.active: dict[str, AdbBridge] = {}  # device_id -> 桥接
        self.ports: dict[str, int] = {}  # device_id -> 本地端口
        self._generation_by_device: dict[str, int] = {}

    def open_session(self, device_id: str, now: float) -> tuple[int, int, str]:
        """开一个新会话世代：世代严格递增（同设备跨会话可比较）、端口稳定、nonce 一次性。

        端口按设备稳定分配且**不回收**：同一台设备每次拿到同一个端口，运维的
        `adb connect` 脚本因此可以写死。设备数量有限，这点分配量不值一提。
        """
        base_port = settings.bridge_base_port
        port = self.ports.setdefault(device_id, base_port + len(self.ports))
        generation = self._generation_by_device.get(device_id, 0) + 1
        self._generation_by_device[device_id] = generation
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

    def remove(self, device_id: str) -> None:
        self.active.pop(device_id, None)

    def reset(self) -> None:
        """清空全部会话状态。

        注册表是进程级单例，端口分配与世代计数会跨用例累积；测试隔离需要
        一个显式重置入口，而不是让用例去动私有字段。
        """
        self.pending.clear()
        self.active.clear()
        self.ports.clear()
        self._generation_by_device.clear()


registry = BridgeRegistry()
