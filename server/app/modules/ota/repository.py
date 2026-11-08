from app.core.db import get_conn


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS ota_release (
            version      TEXT    PRIMARY KEY,
            version_code INTEGER NOT NULL,
            sha256       TEXT    NOT NULL,
            mandatory    INTEGER NOT NULL DEFAULT 0,
            force        INTEGER NOT NULL DEFAULT 0,
            target       TEXT    NOT NULL DEFAULT 'all',
            created_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ota_release_install (
            version    TEXT NOT NULL,
            device_id  TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY (version, device_id)
        );
        """
    )
