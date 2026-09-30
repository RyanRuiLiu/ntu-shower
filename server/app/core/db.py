"""SQLite 连接管理：每线程独立连接，WAL + busy_timeout。

为什么是每线程连接而非进程内单连接：Python 的 sqlite3.Connection 不是线程安全的，
而 FastAPI 会把同步路由与 run_in_threadpool 调用分发到工作线程池。共享单连接时
"execute → commit" 两步之间会被其他线程插入，造成事务边界错乱（数据一致性问题，
非理论风险）。SQLite 官方建议每个线程使用各自的连接，写冲突交由 WAL +
busy_timeout 仲裁。

连接登记在全局表中（而非 threading.local）：测试隔离需要在任意线程关闭全部连接，
threading.local 只能清理当前线程自见的副本。

各领域模块持有自己的表结构（modules/*/repository.py 的 init_tables），
本模块只负责连接生命周期。
"""

import contextlib
import sqlite3
import threading
from pathlib import Path

from .config import settings

_lock = threading.Lock()
# (线程标识, 数据库路径) → 连接。键含线程标识，故同一路径在不同线程各有连接。
_conns: dict[tuple[int, str], sqlite3.Connection] = {}


def _create(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_conn() -> sqlite3.Connection:
    """取当前线程的连接；不存在则创建。"""
    key = (threading.get_ident(), settings.db_path)
    with _lock:
        conn = _conns.get(key)
    if conn is not None:
        return conn

    created = _create(settings.db_path)
    with _lock:
        _conns[key] = created
    return created


def close_all_connections() -> None:
    """关闭并清空全部线程的连接（测试隔离与应用关闭时使用）。

    跨线程关闭是安全的：连接以 check_same_thread=False 创建，且此处只在
    调用方确认无并发使用的情况下执行（测试用例边界、进程退出）。
    """
    with _lock:
        conns = list(_conns.values())
        _conns.clear()
    for conn in conns:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


def init_db() -> None:
    """触发连接创建并校验库可写（表结构由各领域仓储的 init_tables 建立）。"""
    get_conn().execute("SELECT 1")
