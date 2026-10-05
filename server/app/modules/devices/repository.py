"""设备注册表：设备档案（身份、信任、版本、链路时刻）的持久化与查询。

这里不回答"设备在不在线"：在线的唯一事实来源是 core/connections 的内存连接表，
service 在返回记录时把那个答案合并进记录。档案表里不存在线位，否则进程重启后
库里残留的位与内存连接表必然分叉。
"""

import json
from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn, read_one, write

from .schemas import DeviceRecord

#: 读模型的列清单。列名只在此处出现一次，避免多条查询各写一遍后分叉。
_DEVICE_COLUMNS = (
    "device_id, name, trusted, app_version, version_code, os_version, capabilities,"
    " last_seen_at, last_ip, booted_at, clock_skew_s"
)


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS device (
            device_id    TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            trusted      INTEGER NOT NULL DEFAULT 0,
            app_version  TEXT NOT NULL DEFAULT '',
            version_code INTEGER NOT NULL DEFAULT 0,
            os_version   TEXT NOT NULL DEFAULT '',
            capabilities TEXT NOT NULL DEFAULT '[]',
            last_seen_at TEXT NOT NULL DEFAULT '',
            last_ip      TEXT NOT NULL DEFAULT '',
            booted_at    TEXT NOT NULL DEFAULT '',
            -- 设备钟与服务端的偏差（秒）。用来把帧里的 open_time / in_time 平移到
            -- 服务端时间轴——那两个字段是"这次会话从什么时候开始"的唯一线索。
            -- 持久化是必须的：只留内存的话，服务重启后回到 0 等于这段时间不平移，而
            -- "我们刚接手、那边还被占着"恰恰最常发生在设备上线到首次 pong 之间。
            -- 见 core/device_clock。
            clock_skew_s REAL NOT NULL DEFAULT 0
        );

        -- 设备上下线。帧是**事件驱动**的（状态变化才上报），空闲时不上报，所以
        -- "没消息"既可能是"没人用"也可能是"设备离线"——两者在数据里长得一模一样、
        -- 事后无法区分。这张表把它分开，同时给出设备利用率的分母。
        --
        -- 只记"连接建立/断开"这个事实：为什么断（网络、重启、被踢）不在其中，
        -- 那要结合断线前的日志看。
        CREATE TABLE IF NOT EXISTS device_link_event (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            event       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_device_link_event
            ON device_link_event(device_id, occurred_at);
        """
    )


def insert_link_event(device_id: str, event: str, occurred_at: str) -> None:
    """记一次链路事件。重复的 online/offline 不去重——那是如实记录，
    统计侧自己去重（"同一秒连着断三次"本身是网络质量的信号）。"""
    write(
        "INSERT INTO device_link_event (device_id, occurred_at, event) VALUES (?,?,?)",
        (device_id, occurred_at, event),
    )


def link_events(device_id: str, before: str) -> list[Row]:
    """这台设备 `before` 之前的全部上下线事件，按时间升序。

    统计用它在窗口内重建离线区间。**必须能给到窗口之前的事件**：设备在窗口开始前
    就已掉线、直到窗口内才上线的那段同样是离线，只看窗口内的事件会把这段当成在线
    ——而那恰是最常见的掉线形态（夜间关门断电，早上开门才连回来）。
    """
    return (
        get_conn()
        .execute(
            "SELECT occurred_at, event FROM device_link_event"
            " WHERE device_id = ? AND occurred_at < ? ORDER BY occurred_at",
            (device_id, before),
        )
        .fetchall()
    )


def get(device_id: str) -> DeviceRecord | None:
    row = read_one(f"SELECT {_DEVICE_COLUMNS} FROM device WHERE device_id = ?", (device_id,))
    return _as_device(row) if row is not None else None


def list_all() -> list[DeviceRecord]:
    rows = get_conn().execute(f"SELECT {_DEVICE_COLUMNS} FROM device ORDER BY last_seen_at DESC").fetchall()
    return [_as_device(row) for row in rows]


def register(
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: str,
) -> None:
    """上线登记：只覆盖版本、能力、时刻与开机时间，不改 name 与 trusted。"""
    write(
        """
        INSERT INTO device (device_id, app_version, version_code, os_version,
            capabilities, last_seen_at, last_ip, booted_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id) DO UPDATE SET
            app_version  = excluded.app_version,
            version_code = excluded.version_code,
            os_version   = excluded.os_version,
            capabilities = excluded.capabilities,
            last_seen_at = excluded.last_seen_at,
            last_ip      = excluded.last_ip,
            booted_at    = excluded.booted_at
        """,
        (
            device_id,
            app_version,
            version_code,
            os_version,
            json.dumps(capabilities),
            now_rfc3339(),
            ip,
            booted_at,
        ),
    )


def touch_seen(device_id: str) -> None:
    """记下最后一次有该设备消息的时刻；心跳与断开收尾都走这里。

    last_seen_at 的含义是"最后见到"，不区分心跳还是断开。不在每一帧都写：喷头
    帧密集时每秒数帧，心跳粒度足够回答"它还在吗"；"数据还在来吗"看喷头的
    reported_at。
    """
    write("UPDATE device SET last_seen_at = ? WHERE device_id = ?", (now_rfc3339(), device_id))


def set_name(device_id: str, name: str) -> bool:
    return write("UPDATE device SET name = ? WHERE device_id = ?", (name, device_id)).rowcount > 0


def set_trusted(device_id: str, trusted: bool) -> bool:
    """设置信任位；设备不存在时返回 False，信任只能在已有档案上操作。"""
    return (
        write(
            "UPDATE device SET trusted = ? WHERE device_id = ?",
            (1 if trusted else 0, device_id),
        ).rowcount
        > 0
    )


def get_clock_skew(device_id: str) -> float:
    """读这台设备上次记下的钟偏差；档案不存在时为 0。"""
    row = read_one("SELECT clock_skew_s FROM device WHERE device_id = ?", (device_id,))
    return float(row["clock_skew_s"]) if row is not None else 0.0


def set_clock_skew(device_id: str, skew_s: float) -> None:
    """写下这台设备的钟偏差。档案还不存在时什么也不做——偏差是档案的附属，
    而档案由上线登记创建（见 register），这条不该反过来造出一个只有偏差的空档案。"""
    write("UPDATE device SET clock_skew_s = ? WHERE device_id = ?", (skew_s, device_id))


def _as_device(row: Row) -> DeviceRecord:
    """把行转成 DeviceRecord。SQLite 的 0/1 布尔与 JSON 文本数组的转换集中于此。"""
    device: DeviceRecord = {
        "device_id": row["device_id"],
        "name": row["name"],
        "trusted": bool(row["trusted"]),
        "app_version": row["app_version"],
        "version_code": row["version_code"],
        "os_version": row["os_version"],
        "capabilities": _load_capabilities(row["capabilities"]),
        "online": False,  # 占位：由 service 用内存连接表覆盖，见模块注释
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
        "booted_at": row["booted_at"],
        "clock_skew_s": float(row["clock_skew_s"]),
    }
    return device


def _load_capabilities(raw: str) -> list[str]:
    """解析 capabilities 列；内容不是 JSON 数组时按空能力集处理。

    该列内容源自设备上报，解析失败按"未声明"处理，能力校验随之放行（见
    sessions 的能力预检）。
    """
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
