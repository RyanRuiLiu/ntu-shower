"""语音播报三张表的 SQL 集中管理。

`voice_clip` 是内容层（按最终文本合成，多个规则共用），`voice_rule` 是配置层
（文本是模板，带延迟），`voice_delivery` 是投递层（事件 key 去重 + 现场调延迟的
时刻记录）。规则删除不删音频与历史投递。`voice_delivery` 没有清理机制是故意的：
它与统计表同一取向，是"谁在何时被播报了什么"的现场痕迹。

`voice_clip` 的淘汰只删登记行，文件由 service 层删；DB 重置会留下孤儿音频文件，
按 clip_id 与文件名同名，手工清理按这个对应关系来。
"""

from sqlite3 import Row

from app.core.db import get_conn, read_one, write
from app.modules.doors.events import DoorEvent


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS voice_clip (
            clip_id       TEXT PRIMARY KEY,
            sha256        TEXT    NOT NULL,
            size_bytes    INTEGER NOT NULL,
            duration_ms   INTEGER NOT NULL,
            last_used_at  TEXT    NOT NULL,
            created_at    TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS voice_rule (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            cert_no     TEXT    NOT NULL,
            event       TEXT    NOT NULL,
            text        TEXT    NOT NULL,
            delay_ms    INTEGER NOT NULL DEFAULT 0,
            volume      INTEGER NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            priority    INTEGER NOT NULL,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_voice_rule_match
            ON voice_rule (device_id, cert_no, event, enabled, priority, id);

        CREATE TABLE IF NOT EXISTS voice_delivery (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key     TEXT    NOT NULL UNIQUE,
            rule_id       INTEGER NOT NULL,
            device_id     TEXT    NOT NULL,
            door_no       INTEGER NOT NULL,
            cert_no       TEXT    NOT NULL,
            event         TEXT    NOT NULL,
            text          TEXT    NOT NULL,
            clip_id       TEXT    NOT NULL,
            delay_ms      INTEGER NOT NULL,
            occurred_at   TEXT    NOT NULL,
            dispatched_at TEXT    NOT NULL DEFAULT '',
            status        TEXT    NOT NULL,
            detail        TEXT    NOT NULL DEFAULT '',
            trace_id      TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_voice_delivery_created
            ON voice_delivery (created_at);
        """
    )


def insert_rule(
    device_id: str,
    cert_no: str,
    event: str,
    text: str,
    delay_ms: int,
    volume: int,
    enabled: bool,
    priority: int,
    now: str,
) -> int:
    cursor = write(
        "INSERT INTO voice_rule"
        " (device_id, cert_no, event, text, delay_ms, volume, enabled, priority, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (device_id, cert_no, event, text, delay_ms, volume, int(enabled), priority, now, now),
    )
    return int(cursor.lastrowid or 0)


def update_rule(
    rule_id: int,
    cert_no: str,
    event: str,
    text: str,
    delay_ms: int,
    volume: int,
    enabled: bool,
    priority: int,
    now: str,
) -> int:
    cursor = write(
        "UPDATE voice_rule SET cert_no=?, event=?, text=?, delay_ms=?, volume=?,"
        " enabled=?, priority=?, updated_at=? WHERE id=?",
        (cert_no, event, text, delay_ms, volume, int(enabled), priority, now, rule_id),
    )
    return cursor.rowcount


def delete_rule(rule_id: int) -> int:
    return write("DELETE FROM voice_rule WHERE id=?", (rule_id,)).rowcount


def get_rule(rule_id: int) -> Row | None:
    return read_one("SELECT * FROM voice_rule WHERE id=?", (rule_id,))


def list_rules(device_id: str | None) -> list[Row]:
    if device_id:
        return (
            get_conn()
            .execute(
                "SELECT * FROM voice_rule WHERE device_id=? ORDER BY cert_no, event, priority, id",
                (device_id,),
            )
            .fetchall()
        )
    return get_conn().execute("SELECT * FROM voice_rule ORDER BY device_id, cert_no, event, priority, id").fetchall()


def match_rule(device_id: str, cert_no: str, event: str) -> Row | None:
    """同一设备/学号/事件只取启用规则里优先级最小的一条；同值按 id。"""
    return read_one(
        "SELECT id, device_id, cert_no, event, text, delay_ms, volume"
        " FROM voice_rule"
        " WHERE device_id=? AND cert_no=? AND event=? AND enabled=1"
        " ORDER BY priority ASC, id ASC LIMIT 1",
        (device_id, cert_no, event),
    )


def get_clip(clip_id: str) -> Row | None:
    return read_one("SELECT * FROM voice_clip WHERE clip_id=?", (clip_id,))


def upsert_clip(
    clip_id: str,
    sha256: str,
    size_bytes: int,
    duration_ms: int,
    now: str,
) -> None:
    write(
        "INSERT INTO voice_clip (clip_id, sha256, size_bytes, duration_ms, last_used_at, created_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(clip_id) DO UPDATE SET"
        " sha256=excluded.sha256, size_bytes=excluded.size_bytes,"
        " duration_ms=excluded.duration_ms, last_used_at=excluded.last_used_at",
        (clip_id, sha256, size_bytes, duration_ms, now, now),
    )


def touch_clip(clip_id: str, now: str) -> None:
    write("UPDATE voice_clip SET last_used_at=? WHERE clip_id=?", (now, clip_id))


def count_clips() -> int:
    row = read_one("SELECT COUNT(*) AS count FROM voice_clip")
    return int(row["count"]) if row is not None else 0


def prune_clips(max_rows: int, max_bytes: int) -> list[str]:
    """按最近使用淘汰超出上限的音频，返回被删的 clip_id（文件由 service 删）。"""
    rows = (
        get_conn()
        .execute("SELECT clip_id, size_bytes FROM voice_clip ORDER BY last_used_at ASC, clip_id ASC")
        .fetchall()
    )
    total = sum(int(row["size_bytes"]) for row in rows)
    keep = len(rows)
    removed: list[str] = []
    for row in rows:
        if keep <= max_rows and total <= max_bytes:
            break
        clip_id = str(row["clip_id"])
        removed.append(clip_id)
        total -= int(row["size_bytes"])
        keep -= 1
    for clip_id in removed:
        write("DELETE FROM voice_clip WHERE clip_id=?", (clip_id,))
    return removed


def claim_delivery(
    event: DoorEvent,
    rule: Row,
    text: str,
    clip_id: str,
    occurred_at: str,
    trace_id: str,
    now: str,
) -> bool:
    """认领一次事件；同一 `event_key` 只成功一次。"""
    cursor = write(
        "INSERT OR IGNORE INTO voice_delivery"
        " (event_key, rule_id, device_id, door_no, cert_no, event, text, clip_id,"
        " delay_ms, occurred_at, status, trace_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event.event_key,
            int(rule["id"]),
            event.device_id,
            event.door_no,
            event.cert_no,
            event.kind.value,
            text,
            clip_id,
            int(rule["delay_ms"]),
            occurred_at,
            "pending",
            trace_id,
            now,
        ),
    )
    return cursor.rowcount == 1


def finish_delivery(event_key: str, dispatched_at: str, status: str, detail: str) -> None:
    write(
        "UPDATE voice_delivery SET dispatched_at=?, status=?, detail=? WHERE event_key=?",
        (dispatched_at, status, detail, event_key),
    )


def list_deliveries(limit: int) -> list[Row]:
    return (
        get_conn()
        .execute(
            "SELECT id, rule_id, device_id, cert_no, event, door_no, text, delay_ms,"
            " occurred_at, dispatched_at, status, detail"
            " FROM voice_delivery ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        .fetchall()
    )
