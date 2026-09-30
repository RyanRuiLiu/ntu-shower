"""doors 三表 SQL 集中管理：door_live（现态）/ door_state_event（状态转换流水）/ usage_session（洗浴会话）。"""

import json
from sqlite3 import Row
from typing import cast

from app.core.db import get_conn
from app.core.protocol import JsonObject
from app.modules.doors.schemas import DoorView


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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            card_no     INTEGER NOT NULL DEFAULT 0,
            cert_no     TEXT    NOT NULL DEFAULT '',

            -- 三次时刻。语义与旧系统 t_doorlist 的列一一对应：
            -- assigned_at ← openTime（刷卡入门）、opened_at ← inTime（本次开阀）、
            -- ended_at 由服务端在离开占用态时写入（进行中为 NULL）。
            --
            -- opened_at 为空 = 这次分配**从未出过水**，是"算不算使用"的唯一判据。
            -- 注意它记的是**会话中第一次开阀**的时刻：设备的 inTime 会在暂停时清空、
            -- 恢复时重写，直接存最后值会让"上次使用时间"变成最后一次恢复的时刻。
            assigned_at TEXT    NOT NULL,
            opened_at   TEXT,
            ended_at    TEXT,

            -- 本次会话的累计出水秒数。不是协议 bathTime 的副本：设备在暂停时才累加它、
            -- 结束（出门或自动关阀）时整体归零，所以"正在洗浴时直接出门"那一帧里它是 0。
            -- 服务端每次上报都算一次"截至此刻"的时长并取最大值，才不会把这次真实出水
            -- 记成 0 秒。
            flow_s      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_usage_session_door
            ON usage_session(device_id, door_no, opened_at);
        -- 按学号统计（"这个人洗了多久"）走这条；cert_no 才是人，card_no 是卡，
        -- 无卡模式下 card_no 恒为 0，拿它当人查是错的。
        CREATE INDEX IF NOT EXISTS idx_usage_session_cert
            ON usage_session(cert_no, opened_at);
        -- 按时间范围统计（"这个月"）走这条。
        CREATE INDEX IF NOT EXISTS idx_usage_session_time
            ON usage_session(opened_at);
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


def open_session(device_id: str, door_no: int, card_no: int, cert_no: str, assigned_at: str) -> int:
    """开一次新会话，返回会话 id。

    `assigned_at` 取设备的 `openTime`（刷卡入门时刻），为空时由调用方给当前时刻——
    有分配就必然有分配时刻，所以这一列 NOT NULL。
    """
    cursor = get_conn().execute(
        "INSERT INTO usage_session (device_id, door_no, card_no, cert_no, assigned_at) VALUES (?,?,?,?,?)",
        (device_id, door_no, card_no, cert_no, assigned_at),
    )
    get_conn().commit()
    return int(cursor.lastrowid or 0)


def touch_session(session_id: int, opened_at: str, flow_s: int) -> None:
    """推进会话的里程碑。两个字段都只增不减，重复调用无副作用。

    - `opened_at` **只在为空时写入**：它记的是会话中**第一次**开阀的时刻，而设备的
      `inTime` 会在暂停时清空、恢复时重写，跟着它走会让"上次使用时间"漂到最后一次
      恢复的那一刻。
    - `flow_s` **只取更大的值**：调用方给的是"截至此刻的总出水时长"，而设备的
      `bathTime` 在暂停时才累加、结束时整体归零，本身并不单调。
    """
    get_conn().execute(
        "UPDATE usage_session SET"
        " opened_at = CASE WHEN opened_at IS NULL AND ? <> '' THEN ? ELSE opened_at END,"
        " flow_s = MAX(flow_s, ?)"
        " WHERE id = ?",
        (opened_at, opened_at, flow_s, session_id),
    )
    get_conn().commit()


def close_session(session_id: int, ended_at: str) -> None:
    """标记会话结束。

    只写 `ended_at`——`flow_s` 与 `opened_at` 的推进是 `touch_session` 的职责，结束这
    一帧同样先经过它，不在这里重复。

    **不记结束原因**：浴室 App 在"用户主动出门"和三种超时释放下写的是同一套清零语句，
    数据库层面完全同形，服务端没有依据区分。硬分就是编造。真要看"怎么结束的"，
    `door_state_event` 里每次转换都在（`bathing→free` 与 `paused→free` 含义不同）。
    """
    get_conn().execute("UPDATE usage_session SET ended_at = ? WHERE id = ?", (ended_at, session_id))
    get_conn().commit()


def latest_usage_by_door() -> dict[tuple[str, int], Row]:
    """**全部设备**每扇门最后一次真正使用的会话，键是 `(device_id, door_no)`。

    判据是 `opened_at IS NOT NULL`——"分配后直接结算"那种从没出过水的会话因此
    自动排除；而"开了阀立刻关"（`flow_s` 为 0）会被保留，因为确实出过水。

    按 `opened_at` 排序而非 `assigned_at`：排在前面的是"真正开始洗"更晚的那次，
    与人的直觉一致。同一扇门若两条记录 `opened_at` 相同（清库后重开可能发生），
    取 id 更大的那条，避免 `MAX` 匹配到多行时结果不定。

    一次查全部而不是按设备查：管理面可能要不带设备过滤地列全部门，而行数按年计
    也就几万，有 `(device_id, door_no, opened_at)` 索引在，全查比让调用方循环更省。
    """
    rows = (
        get_conn()
        .execute(
            "SELECT device_id, door_no, opened_at, cert_no, flow_s FROM usage_session u"
            " WHERE opened_at IS NOT NULL"
            "   AND u.id = (SELECT id FROM usage_session"
            "               WHERE device_id = u.device_id AND door_no = u.door_no"
            "                 AND opened_at IS NOT NULL"
            "               ORDER BY opened_at DESC, id DESC LIMIT 1)"
        )
        .fetchall()
    )
    return {(row["device_id"], row["door_no"]): row for row in rows}


_DOOR_LIST_SQL = """
    SELECT d.device_id, d.door_no, d.state, d.card_no, d.cert_no, d.acc_no,
           d.open_time, d.in_time, d.close_time, d.bath_time_s, d.status_raw, d.reported_at,
           dev.online AS device_online, dev.name AS device_name
    FROM door_live d LEFT JOIN device dev ON dev.device_id = d.device_id
"""


def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表，联所属设备与上次使用；不指定设备时返回所有设备。

    **只有这一个查询**：任何返回喷头数据的接口都走它。早先按接口面分过三份，
    同一扇门在不同接口里字段不同，前端要写两套渲染——权限管的是能不能调这个接口，
    不是返回什么内容（见 `schemas` 的模块注释）。
    """
    if device_id:
        rows = get_conn().execute(_DOOR_LIST_SQL + " WHERE d.device_id = ? ORDER BY d.door_no", (device_id,)).fetchall()
    else:
        rows = get_conn().execute(_DOOR_LIST_SQL + " ORDER BY d.device_id, d.door_no").fetchall()

    usage = latest_usage_by_door()
    return [_as_door_view(row, usage.get((row["device_id"], row["door_no"]))) for row in rows]


def _as_door_view(row: Row, last_usage: Row | None) -> DoorView:
    """把行转成 DoorView 形状。

    SQLite 用 0/1 存布尔，直接透出会让字段的实际类型与声明不符。LEFT JOIN 无匹配时
    为 NULL，保持 NULL 而不是折成 False——"设备记录不存在"与"设备离线"不是一回事。
    """
    view: DoorView = {
        "device_id": row["device_id"],
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
        "device_online": None if row["device_online"] is None else bool(row["device_online"]),
        "device_name": row["device_name"],
        "last_used_at": "" if last_usage is None else str(last_usage["opened_at"]),
        "last_used_s": 0 if last_usage is None else int(last_usage["flow_s"]),
        "last_used_cert": "" if last_usage is None else str(last_usage["cert_no"]),
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
