"""doors 三表 SQL 集中管理：door_live（现态）/ door_state_event（状态转换流水）/ usage_session（洗浴会话）。"""

import json
from sqlite3 import Row
from typing import cast

from app.core.db import get_conn
from app.core.protocol import JsonObject
from app.modules.doors.schemas import DoorDetail, DoorView


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
            end_reason TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_usage_session_card ON usage_session(card_no, started_at);
        CREATE INDEX IF NOT EXISTS idx_usage_session_door ON usage_session(device_id, door_no, started_at);
        """
    )


def get_all_live(device_id: str) -> dict[int, Row]:
    rows = get_conn().execute("SELECT * FROM door_live WHERE device_id = ?", (device_id,)).fetchall()
    return {r["door_no"]: r for r in rows}


def get_door_live(device_id: str, door_no: int) -> Row | None:
    # sqlite3 的 fetchone 声明为 Any，在此收窄为实际返回的行类型
    return cast(
        "Row | None",
        get_conn()
        .execute("SELECT * FROM door_live WHERE device_id = ? AND door_no = ?", (device_id, door_no))
        .fetchone(),
    )


def get_occupied_by_cert(device_id: str, cert_no: str) -> Row | None:
    """按证件号查占用中的门（无卡模式下占用者标识记录在 cert_no）。"""
    return cast(
        "Row | None",
        get_conn()
        .execute(
            "SELECT * FROM door_live WHERE device_id = ? AND cert_no = ?"
            " AND state IN ('waiting','bathing','paused') LIMIT 1",
            (device_id, cert_no),
        )
        .fetchone(),
    )


def upsert_door_live(device_id: str, door_no: int, state: str, door: JsonObject, reported_at: str) -> None:
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


def insert_event(
    occurred_at: str, device_id: str, door_no: int, from_state: str, to_state: str, detail: JsonObject
) -> None:
    """记录一次状态转换。

    当前没有读取方，但**不是**死代码：它是唯一记录全部八种状态转换的地方
    （usage_session 只覆盖 free→bathing→free 这一条路径），报修次数、暂停次数、
    停用时长这类统计只能由它回答。这类数据删了就回不来，因此先攒着。
    """
    get_conn().execute(
        "INSERT INTO door_state_event (occurred_at, device_id, door_no, from_state, to_state, detail)"
        " VALUES (?,?,?,?,?,?)",
        (occurred_at, device_id, door_no, from_state, to_state, json.dumps(detail, ensure_ascii=False)),
    )
    get_conn().commit()


def get_open_session(device_id: str, door_no: int) -> Row | None:
    return cast(
        "Row | None",
        get_conn()
        .execute(
            "SELECT * FROM usage_session WHERE device_id = ? AND door_no = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (device_id, door_no),
        )
        .fetchone(),
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
    FROM door_live d LEFT JOIN device dev ON dev.device_id = d.device_id
"""


def list_doors_with_device(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表（联设备在线状态与名称）；不指定设备时返回全部设备。"""
    if device_id:
        rows = get_conn().execute(_DOOR_LIST_SQL + " WHERE d.device_id = ? ORDER BY d.door_no", (device_id,)).fetchall()
    else:
        rows = get_conn().execute(_DOOR_LIST_SQL + " ORDER BY d.device_id, d.door_no").fetchall()
    return [_as_door_view(row) for row in rows]


def list_door_details(device_id: str) -> list[DoorDetail]:
    """该设备全部喷头的**完整**现态（door_live 的全列）。

    与 `list_doors_with_device` 的区别在用途：那个是面向多设备的列表视图，只取
    展示必需列；这里供影子文档回答"这台设备现在到底怎么样了"。
    """
    rows = (
        get_conn()
        .execute(
            "SELECT door_no, state, card_no, cert_no, acc_no, open_time, in_time,"
            " close_time, bath_time_s, status_raw, reported_at"
            " FROM door_live WHERE device_id = ? ORDER BY door_no",
            (device_id,),
        )
        .fetchall()
    )
    return [_as_door_detail(row) for row in rows]


def _as_door_detail(row: Row) -> DoorDetail:
    detail: DoorDetail = {
        "door_no": row["door_no"],
        "state": row["state"],
        "card_no": row["card_no"],
        "cert_no": row["cert_no"],
        "acc_no": row["acc_no"],
        "open_time": row["open_time"],
        "in_time": row["in_time"],
        "close_time": row["close_time"],
        "bath_time_s": row["bath_time_s"],
        "status_raw": row["status_raw"],
        "reported_at": row["reported_at"],
    }
    return detail


def _as_door_view(row: Row) -> DoorView:
    """把行转成 DoorView 形状。

    SQLite 用 0/1 存布尔，直接透出会让字段的实际类型与声明不符（devices 模块
    一直在做这个转换，本模块此前漏了）。LEFT JOIN 无匹配时为 NULL，保持 NULL
    而不是折成 False——"设备记录不存在"与"设备离线"不是一回事。
    """
    view: DoorView = {
        "device_id": row["device_id"],
        "door_no": row["door_no"],
        "state": row["state"],
        "card_no": row["card_no"],
        "cert_no": row["cert_no"],
        "bath_time_s": row["bath_time_s"],
        "reported_at": row["reported_at"],
        "device_online": None if row["device_online"] is None else bool(row["device_online"]),
        "device_name": row["device_name"],
    }
    return view


def get_active_by_cert(device_id: str, cert_no: str) -> Row | None:
    """同设备上该凭证的活跃会话（重复分配拦截依据，对齐旧系统 duplicate_session）。"""
    return cast(
        "Row | None",
        get_conn()
        .execute(
            "SELECT * FROM usage_session"
            " WHERE device_id = ? AND cert_no = ? AND ended_at IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (device_id, cert_no),
        )
        .fetchone(),
    )


def list_open_sessions() -> list[Row]:
    """全部设备的活跃会话（自动结算扫描用）。"""
    return get_conn().execute("SELECT * FROM usage_session WHERE ended_at IS NULL").fetchall()
