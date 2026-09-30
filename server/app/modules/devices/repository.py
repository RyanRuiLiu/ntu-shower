"""设备注册表：设备档案（身份/信任/版本/在线状态）的持久化与查询。"""

import json
from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn

from .schemas import DeviceRecord

#: 读模型的列清单。列名只在此处出现一次，避免两条查询各写一遍后逐渐分叉。
_DEVICE_COLUMNS = (
    "device_id, name, trusted, app_version, version_code, os_version, capabilities, online, last_seen_at, last_ip"
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
            online       INTEGER NOT NULL DEFAULT 0,
            last_seen_at TEXT NOT NULL DEFAULT '',
            last_ip      TEXT NOT NULL DEFAULT ''
        );
        """
    )


def get(device_id: str) -> DeviceRecord | None:
    row = get_conn().execute(f"SELECT {_DEVICE_COLUMNS} FROM device WHERE device_id = ?", (device_id,)).fetchone()
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
) -> None:
    """上线登记：只覆盖版本/能力/在线/时间，绝不触碰 name 与 trusted。"""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO device (device_id, app_version, version_code, os_version,
            capabilities, online, last_seen_at, last_ip)
        VALUES (?,?,?,?,?,1,?,?)
        ON CONFLICT(device_id) DO UPDATE SET
            app_version  = excluded.app_version,
            version_code = excluded.version_code,
            os_version   = excluded.os_version,
            capabilities = excluded.capabilities,
            online       = 1,
            last_seen_at = excluded.last_seen_at,
            last_ip      = excluded.last_ip
        """,
        (device_id, app_version, version_code, os_version, json.dumps(capabilities), now_rfc3339(), ip),
    )
    conn.commit()


def set_offline(device_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE device SET online = 0, last_seen_at = ? WHERE device_id = ?",
        (now_rfc3339(), device_id),
    )
    conn.commit()


def set_name(device_id: str, name: str) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE device SET name = ? WHERE device_id = ?", (name, device_id))
    conn.commit()
    return cur.rowcount > 0


def set_trusted(device_id: str, trusted: bool) -> bool:
    """设置信任位；设备不存在时返回 False（信任只能在已有档案上操作）。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE device SET trusted = ? WHERE device_id = ?",
        (1 if trusted else 0, device_id),
    )
    conn.commit()
    return cur.rowcount > 0


def _as_device(row: Row) -> DeviceRecord:
    """把行转成 DeviceRecord。

    SQLite 以 0/1 存布尔、以 JSON 文本存数组，转换集中在此：`get` 与 `list_all`
    此前各写一遍，改一处就会漏另一处。
    """
    device: DeviceRecord = {
        "device_id": row["device_id"],
        "name": row["name"],
        "trusted": bool(row["trusted"]),
        "app_version": row["app_version"],
        "version_code": row["version_code"],
        "os_version": row["os_version"],
        "capabilities": _load_capabilities(row["capabilities"]),
        "online": bool(row["online"]),
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
    }
    return device


def _load_capabilities(raw: str) -> list[str]:
    """解析 capabilities 列；内容不是 JSON 数组时按空能力集处理。

    该列的内容源自设备上报，写库前虽经 json.dumps，但库可能被手工改动过；
    解析不出数组就按空能力集处理，能力校验随之按"未声明"走（见 sessions 的能力预检）。
    """
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
