"""设备注册表：设备档案（身份/信任/版本/在线状态）的持久化与查询。"""

import json

from app.core.db import get_conn


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS devices (
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


def get(device_id: str) -> dict | None:
    row = (
        get_conn()
        .execute(
            "SELECT device_id, name, trusted, app_version, version_code, os_version,"
            " capabilities, online, last_seen_at, last_ip FROM devices WHERE device_id = ?",
            (device_id,),
        )
        .fetchone()
    )
    if row is None:
        return None
    device = dict(row)
    device["trusted"] = bool(device["trusted"])
    device["online"] = bool(device["online"])
    device["capabilities"] = _load_json(device["capabilities"])
    return device


def list_all() -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT device_id, name, trusted, app_version, version_code, os_version, capabilities,"
            " online, last_seen_at, last_ip FROM devices ORDER BY last_seen_at DESC"
        )
        .fetchall()
    )
    devices = []
    for row in rows:
        device = dict(row)
        device["trusted"] = bool(device["trusted"])
        device["online"] = bool(device["online"])
        device["capabilities"] = _load_json(device["capabilities"])
        devices.append(device)
    return devices


def register(device_id: str, app_version: str, version_code: int, os_version: str, capabilities: list, ip: str) -> None:
    """上线登记：只覆盖版本/能力/在线/时间，绝不触碰 name 与 trusted。"""
    from app.core.clock import now_rfc3339

    conn = get_conn()
    conn.execute(
        """
        INSERT INTO devices (device_id, app_version, version_code, os_version,
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
    from app.core.clock import now_rfc3339

    conn = get_conn()
    conn.execute(
        "UPDATE devices SET online = 0, last_seen_at = ? WHERE device_id = ?",
        (now_rfc3339(), device_id),
    )
    conn.commit()


def set_name(device_id: str, name: str) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE devices SET name = ? WHERE device_id = ?", (name, device_id))
    conn.commit()
    return cur.rowcount > 0


def set_trusted(device_id: str, trusted: bool) -> bool:
    """设置信任位；设备不存在时返回 False（信任只能在已有档案上操作）。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE devices SET trusted = ? WHERE device_id = ?",
        (1 if trusted else 0, device_id),
    )
    conn.commit()
    return cur.rowcount > 0


def _load_json(raw: str) -> list[object]:
    """解析 capabilities 列；内容损坏时视为空能力集（不让脏数据拖垮注册）。"""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
