"""语音播报三张表的 SQL 集中管理。

`voice_clip` 是内容层（按最终文本合成，多个规则共用），`voice_rule` 是配置层
（文本是模板，带延迟），`voice_delivery` 是投递层（事件 key 去重 + 现场调延迟用的
时刻与各阶段耗时）。规则删除不删音频与历史投递。`voice_delivery` 没有清理机制是
故意的：它与统计表同一取向，是"谁在何时被播报了什么"的现场痕迹。

`voice_rule` 的目标维度允许 `*`（设备=全部设备、学号=全部学号），`exempt` 是
「全部学号」规则的排除名单（JSON 数组文本，匹配在 service 层做集合判断）。「设备 ×
学号 × 事件」有唯一约束：一个目标只有一条规则，匹配的胜负完全由特异度决定（具体压过
通配），没有需要人来裁的平局。匹配与列表都走特异度排序；规则是人手配置的量级
（几百行封顶），不需要为它建索引，旧的匹配索引已随之移除。

`voice_clip` 的淘汰只删登记行，文件由 service 层删；DB 重置会留下孤儿音频文件，
按 clip_id 与文件名同名，手工清理按这个对应关系来。
"""

import json
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
            exempt      TEXT    NOT NULL DEFAULT '[]',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        DROP INDEX IF EXISTS idx_voice_rule_match;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_rule_target
            ON voice_rule (device_id, cert_no, event);

        CREATE TABLE IF NOT EXISTS voice_effect (
            effect_id   TEXT PRIMARY KEY,
            name        TEXT    NOT NULL,
            size_bytes  INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at  TEXT    NOT NULL
        );

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
            prepared_at   TEXT    NOT NULL DEFAULT '',
            done_at       TEXT    NOT NULL DEFAULT '',
            clip_ms       INTEGER NOT NULL DEFAULT 0,
            downloaded    INTEGER NOT NULL DEFAULT 0,
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
    exempt: str,
    now: str,
) -> int | None:
    """插入一条规则；该目标已有规则时跳过并返回 None（唯一约束的正面表达）。"""
    cursor = write(
        "INSERT INTO voice_rule"
        " (device_id, cert_no, event, text, delay_ms, volume, enabled, exempt, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT (device_id, cert_no, event) DO NOTHING",
        (device_id, cert_no, event, text, delay_ms, volume, int(enabled), exempt, now, now),
    )
    if cursor.rowcount != 1:
        return None
    return int(cursor.lastrowid or 0)


def update_rule(
    rule_id: int,
    device_id: str,
    cert_no: str,
    event: str,
    text: str,
    delay_ms: int,
    volume: int,
    enabled: bool,
    exempt: str,
    now: str,
) -> int:
    cursor = write(
        "UPDATE voice_rule SET device_id=?, cert_no=?, event=?, text=?, delay_ms=?,"
        " volume=?, enabled=?, exempt=?, updated_at=? WHERE id=?",
        (device_id, cert_no, event, text, delay_ms, volume, int(enabled), exempt, now, rule_id),
    )
    return cursor.rowcount


def set_enabled(rule_id: int, enabled: bool, now: str) -> int:
    return write(
        "UPDATE voice_rule SET enabled=?, updated_at=? WHERE id=?",
        (int(enabled), now, rule_id),
    ).rowcount


def delete_rule(rule_id: int) -> int:
    return write("DELETE FROM voice_rule WHERE id=?", (rule_id,)).rowcount


def get_rule(rule_id: int) -> Row | None:
    return read_one("SELECT * FROM voice_rule WHERE id=?", (rule_id,))


def list_rules(device_id: str | None) -> list[Row]:
    """按特异度排序：具体设备在全部设备之前，具体学号在全部学号之前。

    传 `device_id='*'` 时只列通配设备的规则——与具体设备同一套相等筛选，两个
    语义都成立，不另设开关。
    """
    ordering = " ORDER BY (device_id='*') ASC, (cert_no='*') ASC, device_id, cert_no, event, id"
    if device_id:
        return get_conn().execute("SELECT * FROM voice_rule WHERE device_id=?" + ordering, (device_id,)).fetchall()
    return get_conn().execute("SELECT * FROM voice_rule" + ordering).fetchall()


def match_rule(device_id: str, cert_no: str, event: str) -> Row | None:
    """具体压过通配：先比设备维度，再比学号维度，各维度具体值在前。

    「全部学号」的规则带排除名单：名单里有这个学号就跳过它，让位给次特异的
    候选。候选最多四条（两维度各两种取值），在 Python 里做集合判断，不值得
    为它把 JSON 拆进 SQL。
    """
    rows: list[Row] = (
        get_conn()
        .execute(
            "SELECT id, device_id, cert_no, event, text, delay_ms, volume, exempt"
            " FROM voice_rule"
            " WHERE event=? AND enabled=1"
            "   AND device_id IN (?, '*') AND cert_no IN (?, '*')"
            " ORDER BY (device_id='*') ASC, (cert_no='*') ASC, id ASC",
            (event, device_id, cert_no),
        )
        .fetchall()
    )
    for row in rows:
        if row["cert_no"] != "*":
            return row
        if cert_no not in json.loads(str(row["exempt"])):
            return row
    return None


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


def upsert_effect(effect_id: str, name: str, size_bytes: int, duration_ms: int, now: str) -> None:
    """登记一段音效；同一内容（归一化后同哈希）再传就是改名。"""
    write(
        "INSERT INTO voice_effect (effect_id, name, size_bytes, duration_ms, created_at)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(effect_id) DO UPDATE SET name=excluded.name",
        (effect_id, name, size_bytes, duration_ms, now),
    )


def get_effect(effect_id: str) -> Row | None:
    return read_one("SELECT * FROM voice_effect WHERE effect_id=?", (effect_id,))


def list_effects() -> list[Row]:
    return get_conn().execute("SELECT * FROM voice_effect ORDER BY created_at, effect_id").fetchall()


def count_rules_referencing_fx(name: str) -> int:
    """模板里写了 `{fx:名称}` 的规则数（含停用的：停用的规则随时会再启用）。

    用 instr 而不是 LIKE：名称里可能出现 LIKE 的通配符，instr 没有这层语义。
    """
    row = read_one(
        "SELECT COUNT(*) AS count FROM voice_rule WHERE instr(text, '{fx:' || ? || '}') > 0",
        (name,),
    )
    return int(row["count"]) if row is not None else 0


def get_effect_by_name(name: str) -> Row | None:
    return read_one("SELECT * FROM voice_effect WHERE name=?", (name,))


def delete_effect(effect_id: str) -> int:
    return write("DELETE FROM voice_effect WHERE effect_id=?", (effect_id,)).rowcount


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


def mark_clip_ready(event_key: str, prepared_at: str, clip_ms: int) -> None:
    write(
        "UPDATE voice_delivery SET prepared_at=?, clip_ms=? WHERE event_key=?",
        (prepared_at, clip_ms, event_key),
    )


def finish_delivery(
    event_key: str,
    dispatched_at: str,
    status: str,
    detail: str,
    done_at: str,
    downloaded: bool,
) -> None:
    write(
        "UPDATE voice_delivery SET dispatched_at=?, status=?, detail=?, done_at=?, downloaded=? WHERE event_key=?",
        (dispatched_at, status, detail, done_at, int(downloaded), event_key),
    )


def list_deliveries(limit: int) -> list[Row]:
    return (
        get_conn()
        .execute(
            "SELECT id, rule_id, device_id, cert_no, event, door_no, text, delay_ms,"
            " occurred_at, dispatched_at, prepared_at, done_at, clip_ms, downloaded,"
            " status, detail"
            " FROM voice_delivery ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        .fetchall()
    )
