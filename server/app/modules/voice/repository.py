from app.core.db import get_conn

from . import audio

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
    get_conn().executescript(SCHEMA)


# ── 素材 ──
