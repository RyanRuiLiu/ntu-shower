"""FastAPI 依赖。

数据库实例挂在 `app.state.database` 上，由 lifespan 创建、关闭时释放。
依赖只从状态里取，不自己建连接：测试替换临时库时只需换掉 `app.state`，
不用改任何依赖签名。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session

from .session import Database


def get_db(request: Request) -> Iterator[Session]:
    database = cast(Database, request.app.state.database)
    with database.new_session() as session:
        yield session


__all__: list[str] = ["get_db"]
