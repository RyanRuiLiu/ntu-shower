"""后台间隔任务的统一生命周期。

各模块只提供「一轮要干什么」（一个收 `session` 首参的协程函数），启动、取消、
异常留痕与**会话边界**集中在这一处。之前的形态是四个模块各起一个
`while True + sleep` 循环、各写一份 start/stop 与异常日志——同一件事的四份
实现，改一处就要改四处。

主流 asyncio 组织：一个装配点管理全部常驻任务，lifespan 里 start / stop。
每一轮开一个短生命周期会话（一任务一轮一事务），任务函数不再自己管连接。
事件驱动型任务（如 sessions 的按设备 worker、voice 的分配任务）不在这里：
它们不是间隔循环，在各自入口用 `session_scope()` 开会话。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session

from ..db.session import session_scope
from .logging import exc_info_text, log_event


class Background:
    """间隔任务注册表：`interval()` 登记，`start()` 一次启动全部。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, Callable[[Session], Awaitable[None]]]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def interval(self, name: str, seconds: float, fn: Callable[[Session], Awaitable[None]]) -> Background:
        """登记一个每 `seconds` 秒执行一轮的协程 `fn`；每轮以短会话调用它。"""
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

    async def _loop(self, name: str, seconds: float, fn: Callable[[Session], Awaitable[None]]) -> None:
        """统一循环：一轮失败只留痕，循环不退出；取消信号正常上抛。"""
        while True:
            try:
                with session_scope() as session:
                    await fn(session)
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
