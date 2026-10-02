"""SQLite 连接管理：每线程独立连接，WAL 与 busy_timeout。

Python 的 sqlite3.Connection 非线程安全，而同步路由与 run_in_threadpool 会把
调用分发到工作线程；每线程持有独立连接，写冲突交由 WAL 与 busy_timeout 仲裁。
连接登记在全局表而非 threading.local：测试需要在任意线程关闭全部连接。

表结构由各领域仓储的 init_tables 建立，本模块只负责连接生命周期与读写出口。

## 库内命名约定

表名单数 snake_case 带领域前缀；字段名 snake_case，时间字段一律 _at 后缀，取值
一律 RFC 3339 UTC 毫秒字符串（core/clock 是唯一来源）；自增主键叫 id；索引叫
idx_<表>_<列>。唯一例外是协议映射表 door_live：列名保留设备上报的协议字段名，
改名会与协议对不上。
"""

import contextlib
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from sqlite3 import Row
from typing import cast

from .config import settings

_lock = threading.Lock()
# (线程标识, 数据库路径) → 连接。键含线程标识，同一路径在不同线程各有连接。
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


def write(sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
    """执行一条写语句并提交，返回游标（rowcount 与 lastrowid 可用）。

    单条语句加提交是这个库的常态事务边界，收敛于此。多条语句一个事务的批量写
    不经过它：调用方自己持有 get_conn()，显式控制提交时机。
    """
    conn = get_conn()
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor


def read_one(sql: str, params: Sequence[object] = ()) -> Row | None:
    """单行查询。sqlite3 将 fetchone 的返回声明为 Any，在此收窄为实际行类型。"""
    return cast("Row | None", get_conn().execute(sql, params).fetchone())


def close_all_connections() -> None:
    """关闭并清空全部线程的连接（测试隔离与应用关闭时使用）。

    跨线程关闭是安全的：连接以 check_same_thread=False 创建，且此处只在调用方
    确认无并发使用时执行（测试用例边界、进程退出）。
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
