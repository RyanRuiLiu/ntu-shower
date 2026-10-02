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
    "device_id, name, trusted, app_version, version_code, os_version, capabilities, "
    "last_seen_at, last_ip, booted_at, clock_skew_s"
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
            clock_skew_s REAL NOT NULL DEFAULT 0
        );
        """
    )


def set_clock_skew(device_id: str, skew_s: float) -> None:
    """记下设备时钟相对服务端的偏差（秒，正数表示设备快）。

    偏差必须持久化的理由见 core/device_clock 的模块注释。
    """
    write("UPDATE device SET clock_skew_s = ? WHERE device_id = ?", (skew_s, device_id))


def get_clock_skew(device_id: str) -> float:
    """取上次记下的偏差；设备不在档案里时为 0。"""
    row = read_one("SELECT clock_skew_s FROM device WHERE device_id = ?", (device_id,))
    return float(row["clock_skew_s"]) if row is not None else 0.0


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
    快照密集时每秒数帧，心跳粒度足够回答"它还在吗"；"数据还在来吗"看喷头的
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
