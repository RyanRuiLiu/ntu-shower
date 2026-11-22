"""部署入口的关闭序列：1001 先于 uvicorn 的连接收尾。

uvicorn 的 shutdown 先对全部连接发 1012、之后才进入 lifespan——部署入口必须在
它之前发 1001（2026-11-22 生产实证：设备记录 "Code 1012 is reserved"）。入口
用错（退回 uvicorn CLI）会让这道修复整体失效，Dockerfile CMD 一并钉住。
"""

import asyncio
from pathlib import Path

import uvicorn

from app import serve


def test_shutdown_closes_devices_before_uvicorn_touches_connections(
    monkeypatch: object,
) -> None:
    """Server 子类的关闭序列：先发 1001，再进入 uvicorn 自己的收尾。"""
    order: list[str] = []

    async def fake_close() -> None:
        order.append("1001")

    async def fake_super_shutdown(_self: object, sockets: object | None = None) -> None:
        order.append("uvicorn")

    monkeypatch.setattr(serve, "_close_device_connections", fake_close)
    monkeypatch.setattr(uvicorn.Server, "shutdown", fake_super_shutdown)

    asyncio.run(serve.Server(uvicorn.Config("app.main:app")).shutdown())

    assert order == ["1001", "uvicorn"]


def test_dockerfile_runs_the_subclassed_entrypoint() -> None:
    """CMD 用 Server 子类入口：uvicorn CLI 的关闭序列先发 1012，无法补救。"""
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "-m", "app.serve"]' in dockerfile
