"""SQLite 连接管理：进程内单连接，WAL + busy_timeout。

各领域模块持有自己的表结构（modules/*/repository.py 的 init_tables），
本模块只负责连接生命周期。
"""

import sqlite3
import threading
from pathlib import Path

from .config import settings

_lock = threading.Lock()
_conns: dict[str, sqlite3.Connection] = {}


def get_conn() -> sqlite3.Connection:
    """按路径缓存连接（:memory: 每个连接是独立库，路径键保证测试隔离语义正确）。"""
    with _lock:
        path = settings.db_path
        conn = _conns.get(path)
        if conn is None:
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            _conns[path] = conn
        return conn


def init_db() -> None:
    """触发连接创建并校验库可写（表结构由各领域仓储的 init_tables 建立）。"""
    get_conn().execute("SELECT 1")
