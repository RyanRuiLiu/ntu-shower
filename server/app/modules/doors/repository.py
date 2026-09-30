"""doors 三表 SQL 集中管理：door_live（现态）/ door_state_event（变化流水）/ usage_session（洗浴会话）。"""

import json
from sqlite3 import Row

from app.core.db import get_conn


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS door_live (
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            state       TEXT    NOT NULL,
            card_no     INTEGER NOT NULL DEFAULT 0,
            cert_no     TEXT    NOT NULL DEFAULT '',
            acc_no      TEXT    NOT NULL DEFAULT '',
            open_time   TEXT    NOT NULL DEFAULT '',
            in_time     TEXT    NOT NULL DEFAULT '',
            close_time  TEXT    NOT NULL DEFAULT '',
            bath_time_s INTEGER NOT NULL DEFAULT 0,
            status_raw  INTEGER NOT NULL DEFAULT 0,
            reported_at TEXT    NOT NULL,
            PRIMARY KEY (device_id, door_no)
        );

        CREATE TABLE IF NOT EXISTS door_state_event (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT    NOT NULL,
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            from_state  TEXT    NOT NULL,
            to_state    TEXT    NOT NULL,
            detail      TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_door_state_event_door
            ON door_state_event(device_id, door_no, occurred_at);

        CREATE TABLE IF NOT EXISTS usage_session (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id  TEXT    NOT NULL,
            door_no    INTEGER NOT NULL,
            card_no    INTEGER NOT NULL,
            cert_no    TEXT    NOT NULL DEFAULT '',
            started_at TEXT,
            ended_at   TEXT,
            duration_s INTEGER,
            end_reason TEXT    NOT NULL DEFAULT '',
            raw        TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_usage_session_card ON usage_session(card_no, started_at);
        CREATE INDEX IF NOT EXISTS idx_usage_session_door ON usage_session(device_id, door_no, started_at);
        """
    )


def get_all_live(device_id: str) -> dict[int, Row]:
    rows = get_conn().execute("SELECT * FROM door_live WHERE device_id = ?", (device_id,)).fetchall()
    return {r["door_no"]: r for r in rows}


def get_door_live(device_id: str, door_no: int) -> Row | None:
    return (
        get_conn()
        .execute("SELECT * FROM door_live WHERE device_id = ? AND door_no = ?", (device_id, door_no))
        .fetchone()
    )


def get_occupied_by_cert(device_id: str, cert_no: str) -> Row | None:
    """按证件号查占用中的门（无卡模式下占用者标识记录在 cert_no）。"""
    return (
        get_conn()
        .execute(
            "SELECT * FROM door_live WHERE device_id = ? AND cert_no = ?"
            " AND state IN ('waiting','bathing','paused') LIMIT 1",
            (device_id, cert_no),
        )
        .fetchone()
    )


def upsert_door_live(device_id: str, door_no: int, state: str, door: dict, reported_at: str) -> None:
    get_conn().execute(
        """
        INSERT INTO door_live (device_id, door_no, state, card_no, cert_no, acc_no,
                               open_time, in_time, close_time, bath_time_s, status_raw, reported_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id, door_no) DO UPDATE SET
            state=excluded.state, card_no=excluded.card_no, cert_no=excluded.cert_no,
            acc_no=excluded.acc_no, open_time=excluded.open_time, in_time=excluded.in_time,
            close_time=excluded.close_time, bath_time_s=excluded.bath_time_s,
            status_raw=excluded.status_raw, reported_at=excluded.reported_at
        """,
        (
            device_id,
            door_no,
            state,
            int(door.get("card_no") or 0),
            str(door.get("cert_no") or ""),
            str(door.get("acc_no") or ""),
            str(door.get("open_time") or ""),
            str(door.get("in_time") or ""),
            str(door.get("close_time") or ""),
            int(door.get("bath_time_s") or 0),
            int(door.get("status_raw") or 0),
            reported_at,
        ),
    )
    get_conn().commit()


def insert_event(occurred_at: str, device_id: str, door_no: int, from_state: str, to_state: str, detail: dict) -> None:
    get_conn().execute(
        "INSERT INTO door_state_event (occurred_at, device_id, door_no, from_state, to_state, detail)"
        " VALUES (?,?,?,?,?,?)",
        (occurred_at, device_id, door_no, from_state, to_state, json.dumps(detail, ensure_ascii=False)),
    )
    get_conn().commit()


def get_open_session(device_id: str, door_no: int) -> Row | None:
    return (
        get_conn()
        .execute(
            "SELECT * FROM usage_session WHERE device_id = ? AND door_no = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (device_id, door_no),
        )
        .fetchone()
    )


def open_session(device_id: str, door_no: int, card_no: int, cert_no: str, started_at: str) -> None:
    get_conn().execute(
        "INSERT INTO usage_session (device_id, door_no, card_no, cert_no, started_at) VALUES (?,?,?,?,?)",
        (device_id, door_no, card_no, cert_no, started_at),
    )
    get_conn().commit()


def close_session(session_id: int, ended_at: str, duration_s: int, end_reason: str) -> None:
    get_conn().execute(
        "UPDATE usage_session SET ended_at = ?, duration_s = ?, end_reason = ? WHERE id = ?",
        (ended_at, duration_s, end_reason, session_id),
    )
    get_conn().commit()


_DOOR_LIST_SQL = """
    SELECT d.device_id, d.door_no, d.state, d.card_no, d.cert_no, d.bath_time_s,
           d.reported_at, dev.online AS device_online, dev.name AS device_name
    FROM door_live d LEFT JOIN devices dev ON dev.device_id = d.device_id
"""


def list_doors_with_device(device_id: str | None = None) -> list[dict]:
    """喷头现态列表（联设备在线状态与名称）；不指定设备时返回全部设备。"""
    if device_id:
        rows = get_conn().execute(_DOOR_LIST_SQL + " WHERE d.device_id = ? ORDER BY d.door_no", (device_id,)).fetchall()
    else:
        rows = get_conn().execute(_DOOR_LIST_SQL + " ORDER BY d.device_id, d.door_no").fetchall()
    return [dict(r) for r in rows]


def get_active_by_cert(device_id: str, cert_no: str) -> Row | None:
    """同设备上该凭证的活跃会话（重复分配拦截依据，对齐旧系统 duplicate_session）。"""
    row = (
        get_conn()
        .execute(
            "SELECT * FROM usage_session"
            " WHERE device_id = ? AND cert_no = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (device_id, cert_no),
        )
        .fetchone()
    )
    return row


def list_open_sessions() -> list[Row]:
    """全部设备的活跃会话（自动结算扫描用）。"""
    return get_conn().execute("SELECT * FROM usage_session WHERE ended_at IS NULL").fetchall()
