"""会话工厂与 FastAPI 依赖。

引擎按 URL 单例缓存：SQLAlchemy 官方模型是"Engine 进程内只创建一次、自持连接
池"（docs.sqlalchemy.org/tutorial/engine.html）——连接池的生命周期必须与引擎
一致，按次创建再销毁等于连接池永不生效。URL 变化（测试换库、配置切换）即换库：
旧引擎归还、新引擎按新 URL 建立。

`expire_on_commit=False`：提交后对象仍可读。默认 True 会让所有属性在提交后过期，
序列化响应时再触发一轮 SELECT，把刚写进去的数据又查一遍。
`autoflush=False`：查询不隐式刷写，写库时机由事务边界决定，不靠框架猜。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from .engine import create_db_engine


class Database:
    """数据库连接的显式生命周期：引擎、会话工厂、释放。

    两条使用路径的生命周期语义不同：
    - **显式持有**（app.state、测试装配）：构造持引擎，宿主结束时 dispose——
      连接池与宿主同寿；
    - **随用随取**（session_scope）：走 [_database_for] 的按 URL 单例，进程内
      复用，不逐次销毁。
    """

    def __init__(self, url: str) -> None:
        self.engine = create_db_engine(url)
        self._session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )

    def new_session(self) -> Session:
        """新建一个会话，供脚本、迁移与测试使用。

        与 `session()` 的区别：这里返回会话对象，由调用方负责提交与关闭；
        `session()` 是给 FastAPI 依赖用的生成器，请求结束自动关闭。
        """
        return self._session_factory()

    def session(self) -> Iterator[Session]:
        """FastAPI 依赖：每个请求一个会话，请求结束自动关闭。"""
        with self._session_factory() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()


_lock = threading.Lock()
_database: Database | None = None
_database_url: str = ""


def _database_for(url: str) -> Database:
    """按 URL 取随用随取的 Database：同 URL 复用（连接池因此生效），URL 变化
    （测试换库）即归还旧的、按新 URL 建立。加锁只护住创建窗口，会话的使用
    在锁外。"""
    global _database, _database_url
    with _lock:
        if _database is None or _database_url != url:
            if _database is not None:
                _database.dispose()
            _database = Database(url)
            _database_url = url
        return _database


@contextmanager
def session_scope() -> Iterator[Session]:
    """非请求路径（后台任务、asyncio 任务、脚本）的短生命周期会话。

    请求路径用 `Depends(get_db)`，WS 路径从 `app.state.database` 取；这三者
    覆盖全部会话来源，没有别的入口。事务边界与请求对齐：正常退出提交，
    异常时回滚，会话总是关闭。
    """
    from app.core.config import settings

    database = _database_for(settings.effective_database_url)
    with database.new_session() as session:
        yield session
        session.commit()


__all__: list[str] = ["Database", "session_scope"]
