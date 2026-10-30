"""固定语音素材、规则与投递的 SQL 集中管理。

`voice_asset` 是素材层（文件身份就是归一化后的 SHA-256）；`voice_rule` 是配置层
（规则保存一串分段，不再保存文本/音色）；`voice_delivery` 是投递层（事件 key 去重 +
解析后的片段摘要与结果）。

规则删除不删素材；素材被规则引用时不允许删除。设备端缓存按素材哈希命中，同一个
内容只下载一次。

## 一次性重建

旧版本的语音表（TTS clip、effect、text 规则、投递）与固定音频模型不兼容。首次启动新代码
时把旧 `voice_effect` 文件导入 `voice_asset`，随后删除旧规则/投递/合成缓存表，并
清掉生成的 TTS clip 文件；旧 effect 文件保留（已导入新表）。之后不再走这段。
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from sqlite3 import Row

from app.core.config import settings
from app.core.db import get_conn, read_one, write
from app.core.logging import log_event
from app.modules.doors.events import DoorEvent

from . import audio
from . import time_windows as time_rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_asset (
    asset_id    TEXT PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    digit       INTEGER UNIQUE,
    duration_ms INTEGER NOT NULL,
    size_bytes  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_unit (
    unit     TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS voice_rule (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    devices      TEXT    NOT NULL,
    certs        TEXT    NOT NULL,
    events       TEXT    NOT NULL,
    time_windows TEXT    NOT NULL DEFAULT '[]',
    exempt       TEXT    NOT NULL DEFAULT '[]',
    segments     TEXT    NOT NULL,
    volume      INTEGER NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS voice_delivery (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key     TEXT    NOT NULL UNIQUE,
    rule_id       INTEGER NOT NULL,
    device_id     TEXT    NOT NULL,
    door_no       INTEGER NOT NULL,
    cert_no       TEXT    NOT NULL,
    event         TEXT    NOT NULL,
    segments      TEXT    NOT NULL,
    segment_count INTEGER NOT NULL,
    duration_ms   INTEGER NOT NULL,
    occurred_at   TEXT    NOT NULL,
    dispatched_at TEXT    NOT NULL DEFAULT '',
    done_at       TEXT    NOT NULL DEFAULT '',
    downloaded    INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL,
    detail        TEXT    NOT NULL DEFAULT '',
    trace_id      TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_delivery_created
    ON voice_delivery (created_at);
"""


def init_tables() -> None:
    audio.assets_dir().mkdir(parents=True, exist_ok=True)
    audio.tmp_dir().mkdir(parents=True, exist_ok=True)
    _rebuild_legacy_if_needed()
    _reset_rule_schema_if_outdated()
    get_conn().executescript(SCHEMA)


def _table_exists(name: str) -> bool:
    row = read_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return row is not None


def _columns(name: str) -> list[str]:
    if not _table_exists(name):
        return []
    return [str(row["name"]) for row in get_conn().execute(f"PRAGMA table_info({name})")]


def _reset_rule_schema_if_outdated() -> None:
    """规则表结构换代：没有 time_windows 的旧表直接丢弃重建。

    这是新表结构上线时的一次性重建，不保留旧规则；调用方应在部署前导出旧规则，
    部署后按新结构回填。旧表若仍是旧 TTS 模型，[`_rebuild_legacy_if_needed`] 已经
    处理过，这里只负责固定素材模型之后新增的时间列。
    """
    columns = _columns("voice_rule")
    if columns and "time_windows" not in columns:
        log_event("WARNING", "voice.rule-schema-reset", "语音规则表结构换代，清空并重建 voice_rule")
        get_conn().execute("DROP TABLE voice_rule")
        get_conn().commit()


def _rebuild_legacy_if_needed() -> None:
    """旧 TTS/音效模型 → 固定素材模型的一次性重建。"""
    rule_columns = _columns("voice_rule")
    delivery_columns = _columns("voice_delivery")
    legacy = (
        _table_exists("voice_effect")
        or _table_exists("voice_clip")
        or (rule_columns and "segments" not in rule_columns)
        or (delivery_columns and "clip_id" in delivery_columns)
    )
    if not legacy:
        return

    log_event("WARNING", "voice.legacy-rebuild", "语音模块重建：导入旧音效并清理旧表")
    _create_asset_table()
    _import_legacy_effects()
    for table in ("voice_rule", "voice_delivery", "voice_clip", "voice_effect"):
        get_conn().execute(f"DROP TABLE IF EXISTS {table}")
    _cleanup_generated_clips()
    get_conn().commit()


def _create_asset_table() -> None:
    get_conn().execute(
        """
        CREATE TABLE IF NOT EXISTS voice_asset (
            asset_id    TEXT PRIMARY KEY,
            name        TEXT    NOT NULL UNIQUE,
            digit       INTEGER UNIQUE,
            duration_ms INTEGER NOT NULL,
            size_bytes  INTEGER NOT NULL,
            created_at  TEXT    NOT NULL
        )
        """
    )


def _import_legacy_effects() -> None:
    from app.core.clock import now_rfc3339

    if not _table_exists("voice_effect"):
        return
    now = now_rfc3339()
    for row in get_conn().execute("SELECT * FROM voice_effect ORDER BY created_at").fetchall():
        effect_id = str(row["effect_id"])
        name = str(row["name"])
        if get_asset_by_name(name) is not None:
            continue
        source = audio.legacy_effects_dir() / f"{effect_id}.wav"
        if not source.is_file():
            log_event("WARNING", "voice.legacy-effect-missing", "旧音效文件不存在", effect_id=effect_id)
            continue
        try:
            target = audio.tmp_dir() / f"legacy-{effect_id}.mp3"
            duration_ms = audio.normalize_to_mp3(source, target)
            asset_id = audio.sha256_file(target)
            final = audio.asset_path(asset_id)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                target.unlink(missing_ok=True)
            else:
                target.replace(final)
            write(
                "INSERT OR IGNORE INTO voice_asset(asset_id,name,digit,duration_ms,size_bytes,created_at)"
                " VALUES (?,?,NULL,?,?,?)",
                (asset_id, name, duration_ms, final.stat().st_size, now),
            )
        except Exception as exc:  # noqa: BLE001 — 单个旧音效失败不能挡住启动
            log_event(
                "WARNING",
                "voice.legacy-effect-import-failed",
                "旧音效导入失败",
                effect_id=effect_id,
                error=str(exc),
            )
    get_conn().commit()


def _cleanup_generated_clips() -> None:
    """旧 TTS 合成缓存是孤儿；旧 effect 文件保留（已导入新表）。"""
    clips = Path(settings.voice_dir) / "clips"
    if clips.is_dir():
        try:
            shutil.rmtree(clips)
        except OSError as exc:
            log_event("WARNING", "voice.legacy-cleanup-failed", "旧 TTS 缓存清理失败", path=str(clips), reason=str(exc))
    tmp = audio.tmp_dir()
    if tmp.is_dir():
        for path in tmp.glob("*"):
            if path.is_file():
                path.unlink(missing_ok=True)


# ── 素材 ──


def list_assets() -> list[Row]:
    return get_conn().execute("SELECT * FROM voice_asset ORDER BY digit IS NULL, digit, name").fetchall()


def get_asset(asset_id: str) -> Row | None:
    return read_one("SELECT * FROM voice_asset WHERE asset_id=?", (asset_id,))


def get_asset_by_name(name: str) -> Row | None:
    return read_one("SELECT * FROM voice_asset WHERE name=?", (name,))


def get_asset_by_digit(digit: int) -> Row | None:
    return read_one("SELECT * FROM voice_asset WHERE digit=?", (digit,))


def unit_rows() -> dict[str, Row]:
    return {
        str(row["unit"]): row
        for row in get_conn().execute(
            "SELECT a.*, u.unit FROM voice_unit u JOIN voice_asset a ON a.asset_id = u.asset_id"
        )
    }


def get_unit_by_asset(asset_id: str) -> Row | None:
    return read_one("SELECT * FROM voice_unit WHERE asset_id=?", (asset_id,))


def insert_unit(unit: str, asset_id: str) -> None:
    write("INSERT INTO voice_unit(unit, asset_id) VALUES (?,?)", (unit, asset_id))


def delete_unit(asset_id: str) -> None:
    write("DELETE FROM voice_unit WHERE asset_id=?", (asset_id,))


def insert_asset(asset_id: str, name: str, digit: int | None, duration_ms: int, size_bytes: int, now: str) -> None:
    write(
        "INSERT INTO voice_asset(asset_id,name,digit,duration_ms,size_bytes,created_at) VALUES (?,?,?,?,?,?)",
        (asset_id, name, digit, duration_ms, size_bytes, now),
    )


def rename_asset(asset_id: str, name: str) -> int:
    return write("UPDATE voice_asset SET name=? WHERE asset_id=?", (name, asset_id)).rowcount


def delete_asset(asset_id: str) -> int:
    return write("DELETE FROM voice_asset WHERE asset_id=?", (asset_id,)).rowcount


def asset_rows_by_ids(asset_ids: list[str]) -> dict[str, Row]:
    if not asset_ids:
        return {}
    placeholders = ",".join("?" for _ in asset_ids)
    rows = (
        get_conn().execute(f"SELECT * FROM voice_asset WHERE asset_id IN ({placeholders})", tuple(asset_ids)).fetchall()
    )
    return {str(row["asset_id"]): row for row in rows}


# ── 规则 ──


def insert_rule(
    devices: str,
    certs: str,
    events: str,
    time_windows: str,
    exempt: str,
    segments: str,
    volume: int,
    enabled: bool,
    now: str,
) -> int:
    cursor = write(
        "INSERT INTO voice_rule(devices,certs,events,time_windows,exempt,segments,volume,enabled,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (devices, certs, events, time_windows, exempt, segments, volume, int(enabled), now, now),
    )
    return int(cursor.lastrowid or 0)


def update_rule(
    rule_id: int,
    devices: str,
    certs: str,
    events: str,
    time_windows: str,
    exempt: str,
    segments: str,
    volume: int,
    enabled: bool,
    now: str,
) -> int:
    cursor = write(
        "UPDATE voice_rule SET devices=?, certs=?, events=?, time_windows=?, exempt=?, segments=?, volume=?,"
        " enabled=?, updated_at=? WHERE id=?",
        (devices, certs, events, time_windows, exempt, segments, volume, int(enabled), now, rule_id),
    )
    return cursor.rowcount


def set_enabled(rule_id: int, enabled: bool, now: str) -> int:
    return write(
        "UPDATE voice_rule SET enabled=?, updated_at=? WHERE id=?",
        (int(enabled), now, rule_id),
    ).rowcount


def get_rule(rule_id: int) -> Row | None:
    return read_one("SELECT * FROM voice_rule WHERE id=?", (rule_id,))


def list_rules() -> list[Row]:
    return get_conn().execute("SELECT * FROM voice_rule ORDER BY id").fetchall()


def delete_rule(rule_id: int) -> int:
    return write("DELETE FROM voice_rule WHERE id=?", (rule_id,)).rowcount


def rule_priority(
    devices: list[str], certs: list[str], time_windows: list[time_rules.Window]
) -> tuple[int, int, int, int, int, int]:
    """规则匹配优先级：设备 → 学号 → 集合大小 → 时段。

    前三项沿用原模型：设备具体先于学号具体，小集合先于大集合。时间只在前四项
    完全相同时参与：有时段比不限时段紧，一周覆盖分钟数更少的时段更紧。负号让降序
    排序把更紧的规则排在前面；规则 id 不参与优先级。
    """
    device_specific = 0 if "*" in devices else 1
    cert_specific = 0 if "*" in certs else 1
    device_width = len(devices) if device_specific else 0
    cert_width = len(certs) if cert_specific else 0
    time_specific = 1 if time_windows else 0
    time_coverage = time_rules.coverage(time_windows)
    return (device_specific, cert_specific, -device_width, -cert_width, time_specific, -time_coverage)


def matching_rules(device_id: str, cert_no: str, event: str, local: datetime) -> list[Row]:
    """一次事件命中的启用规则，按优先级排序；完全同优先级时拒绝替用户挑一条。"""
    hits: list[tuple[tuple[int, int, int, int, int, int], Row]] = []
    for row in get_conn().execute("SELECT * FROM voice_rule WHERE enabled=1").fetchall():
        if event not in json.loads(str(row["events"])):
            continue
        devices = json.loads(str(row["devices"]))
        certs = json.loads(str(row["certs"]))
        if not ("*" in devices or device_id in devices):
            continue
        if "*" in certs:
            if cert_no in json.loads(str(row["exempt"])):
                continue
        elif cert_no not in certs:
            continue
        try:
            windows = time_rules.parse_json(str(row["time_windows"]))
        except ValueError as exc:
            log_event(
                "ERROR",
                "voice.rule-time-invalid",
                "规则时段格式非法，跳过",
                rule_id=int(row["id"]),
                error=str(exc),
            )
            continue
        if not time_rules.match(windows, local):
            log_event(
                "DEBUG",
                "voice.rule-time-filtered",
                "规则不在生效时段内",
                device_id=device_id,
                event=event,
                rule_id=int(row["id"]),
                local_time=local.isoformat(timespec="minutes"),
            )
            continue
        hits.append((rule_priority(devices, certs, windows), row))
    hits.sort(key=lambda item: item[0], reverse=True)
    if len(hits) >= 2 and hits[0][0] == hits[1][0]:
        log_event(
            "ERROR",
            "voice.rule-ambiguous",
            "同优先级规则同时命中，拒绝随机选择",
            device_id=device_id,
            event=event,
            local_time=local.isoformat(timespec="minutes"),
            rule_ids=[int(hits[0][1]["id"]), int(hits[1][1]["id"])],
        )
        return []
    return [row for _priority, row in hits]


# ── 投递 ──


def claim_delivery(
    event: DoorEvent,
    rule_id: int,
    segments: str,
    segment_count: int,
    duration_ms: int,
    occurred_at: str,
    trace_id: str,
    now: str,
) -> bool:
    cursor = write(
        "INSERT OR IGNORE INTO voice_delivery"
        " (event_key,rule_id,device_id,door_no,cert_no,event,segments,segment_count,duration_ms,"
        " occurred_at,status,trace_id,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event.event_key,
            rule_id,
            event.device_id,
            event.door_no,
            event.cert_no,
            event.kind.value,
            segments,
            segment_count,
            duration_ms,
            occurred_at,
            "pending",
            trace_id,
            now,
        ),
    )
    return cursor.rowcount == 1


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
            "SELECT id,event_key,rule_id,device_id,cert_no,event,door_no,segments,segment_count,"
            "duration_ms,occurred_at,dispatched_at,done_at,status,detail,downloaded"
            " FROM voice_delivery ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        .fetchall()
    )
