"""数据库引擎：连接参数与 SQLite 的 PRAGMA。

三条 PRAGMA 缺一不可：
- `journal_mode=WAL`：读不阻塞写、写不阻塞读；代价是必须同机访问。
- `busy_timeout`：写冲突时排队等待而不是立刻抛 SQLITE_BUSY。
- `foreign_keys=ON`：SQLite 默认**不**强制外键，必须每条连接单独打开。

PRAGMA 是连接级状态，所以挂在 `connect` 事件上，而不是执行一次了事。
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event

BUSY_TIMEOUT_MS = 5000


def create_db_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


__all__: list[str] = ["BUSY_TIMEOUT_MS", "create_db_engine"]
