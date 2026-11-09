"""后台间隔任务的统一生命周期。

各模块只提供「一轮要干什么」（一个无参协程），启动、取消、异常留痕集中在这一处。
之前的形态是四个模块各起一个 `while True + sleep` 循环、各写一份 start/stop 与
异常日志——同一件事的四份实现，改一处就要改四处。

主流 asyncio 组织：一个装配点管理全部常驻任务，lifespan 里 start / stop。
事件驱动型任务（如 sessions 的按设备 worker、voice 的分配任务）不在这里：
它们不是间隔循环，在各自入口用 `session_scope()` 开会话。

会话边界归任务自己（一任务一轮一事务），本模块不再注入会话。原因：把事件循环
线程上建好的 ORM Session 交给线程池（status_line 在线程里读库）不受 ORM 支持——
同一会话被两条线程并发使用，SQLite 直接 access violation，测试并行时的
worker 崩溃就是它。会话在哪个线程里生，就在哪个线程里关。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .logging import exc_info_text, log_event


class Background:
    """间隔任务注册表：`interval()` 登记，`start()` 一次启动全部。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Callable[[], Awaitable[None]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def interval(self, name: str, seconds: float, fn: Callable[[], Awaitable[None]]) -> Background:
        """登记一个每 `seconds` 秒执行一轮的协程 `fn`；会话由它自己开。"""
        self._entries[name] = (seconds, fn)
        return self

    def start(self) -> None:
        for name, (seconds, fn) in self._entries.items():
            if self._tasks.get(name) is None or self._tasks[name].done():
                self._tasks[name] = asyncio.create_task(self._loop(name, seconds, fn))
                log_event("INFO", f"{name}.started", f"后台任务已启动：{name}")

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, name: str, seconds: float, fn: Callable[[], Awaitable[None]]) -> None:
        """统一循环：一轮失败只留痕，循环不退出；取消信号正常上抛。"""
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不得终止后台任务
                log_event(
                    "ERROR",
                    f"{name}.failed",
                    "后台任务一轮失败，等下一轮重试",
                    error=exc_info_text() or str(exc),
                )
            await asyncio.sleep(seconds)


__all__: list[str] = ["Background"]
