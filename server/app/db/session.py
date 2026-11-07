"""会话工厂与 FastAPI 依赖。

`expire_on_commit=False`：提交后对象仍可读。默认 True 会让所有属性在提交后过期，
序列化响应时再触发一轮 SELECT，把刚写进去的数据又查一遍。
`autoflush=False`：查询不隐式刷写，写库时机由事务边界决定，不靠框架猜。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session, sessionmaker

from .engine import create_db_engine


class Database:
    """数据库连接的唯一入口：引擎、会话工厂、生命周期。"""

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


def session_scope() -> Iterator[Session]:
    """非请求路径（后台任务、仓储层的迁移期封装）的短生命周期会话。

    请求路径用 `Database.session()` 依赖注入；后台任务没有请求上下文，用这一个
    入口按当前配置开短会话。迁移期仓储层还保留旧签名（无 session 参数），
    内部也走这里，全部调用点迁到显式注入后删除。
    """
    from app.core.config import settings

    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            yield session
    finally:
        database.dispose()


__all__: list[str] = ["Database", "session_scope"]
